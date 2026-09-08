"""One bounded document worker; queue/progress live in SQL, restart requires explicit retry."""
import logging
import threading
import uuid
from pathlib import Path

from fastapi import HTTPException

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models import Document
from app.rag.ingest import ingest_document
from app.rag.identity import EmbeddingError
from app.rag.maintenance import save, state, sync_index
from app.runs import LOCK

logger = logging.getLogger(__name__)
ACTIVE = {'pending', 'queued', 'processing'}
WORK_LOCK = threading.Lock()  # serializes worker steps; offline operations exclude the server


def enqueue(db, doc):
    with LOCK:
        doc = db.get(Document, doc.id, populate_existing=True)
        if not doc:
            raise HTTPException(404, 'Document not found')
        if db.query(Document).filter(Document.status.in_(ACTIVE), Document.id != doc.id).count() >= 32:
            raise HTTPException(429, 'Document queue is full. Wait for processing to finish.')
        if doc.status in {'queued', 'processing'}:
            raise HTTPException(409, 'Document is already processing.')
        path = next(UPLOADS_DIR.glob(f'{doc.id}_*'), None)
        if not path or not path.is_file():
            raise HTTPException(409, 'Original upload is unavailable. Upload the document again.')
        doc.status, doc.error = 'queued', None
        doc.ingestion = {**(doc.ingestion or {}), 'token': uuid.uuid4().hex, 'stage': 'queued',
                         'attempt': (doc.ingestion or {}).get('attempt', 0) + 1,
                         'completed': 0, 'total': 0}
        db.commit()
    worker.wake.set()
    return doc


def queue_rebuild(db):
    with LOCK:
        current = state(db)
        if current.get('status') in {'queued', 'processing'}:
            raise HTTPException(409, 'Index rebuild already queued or processing.')
        save(db, {**current, 'status': 'queued', 'error': None, 'completed': 0, 'total': 0})
    worker.wake.set()
    return state(db)


class Worker:
    def __init__(self, factory=SessionLocal):
        self.factory, self.thread = factory, None
        self.stopping, self.wake = threading.Event(), threading.Event()

    def recover(self):
        with LOCK, self.factory() as db:
            for doc in db.query(Document).filter(Document.status.in_(ACTIVE)).all():
                doc.status, doc.error = 'interrupted', 'Processing was interrupted by restart. Retry to continue.'
                doc.ingestion = {**(doc.ingestion or {}), 'stage': 'interrupted'}
            db.commit()
            current = state(db)
            if current.get('status') in {'queued', 'processing'}:
                save(db, {**current, 'status': 'interrupted', 'error': 'Rebuild interrupted. Retry; previous index preserved.'})

    def start(self):
        self.recover()
        self.stopping.clear()
        self.thread = threading.Thread(target=self.loop, name='phlox-documents', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join()
            self.thread = None

    def loop(self):
        while not self.stopping.is_set():
            try:
                if not self.step():
                    self.wake.wait(1)
                    self.wake.clear()
            except Exception:
                logger.exception('Document worker stopped after a persistence failure; restart to recover')
                self.stopping.set()

    def step(self):
        with WORK_LOCK, self.factory() as db:
            with LOCK:
                current = state(db)
                if current.get('status') == 'queued':
                    save(db, {**current, 'status': 'processing'})
                    rebuild = True
                    doc = None
                else:
                    rebuild = False
                    doc = db.query(Document).filter_by(status='queued').order_by(Document.created_at).first()
                    if not doc:
                        return False
                    doc.status = 'processing'
                    doc.ingestion = {**(doc.ingestion or {}), 'stage': 'starting'}
                    db.commit()
            if rebuild:
                def check():
                    if self.stopping.is_set():
                        raise RuntimeError('Worker stopping')
                try:
                    result = sync_index(db, check)
                    with LOCK:
                        save(db, {**state(db), 'status': 'ready', 'error': None, **result})
                except Exception as exc:
                    db.rollback()
                    detail = str(exc) if isinstance(exc, EmbeddingError) else 'Check embedding service and retry.'
                    logger.warning('Document index rebuild ended: %s', type(exc).__name__)
                    with LOCK:
                        save(db, {**state(db), 'status': 'error', 'error': 'Rebuild failed or interrupted. Previous index preserved. ' + detail})
                return True
            path = next(UPLOADS_DIR.glob(f'{doc.id}_*'), Path('/missing-upload'))
            ingest_document(db, doc, path, self.stopping.is_set)
            return True


worker = Worker()
