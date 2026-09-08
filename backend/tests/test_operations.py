"""Populated upgrade/restore drills. Postgres uses disposable databases only.

Set PHLOX_TEST_POSTGRES_URL to a test server whose role may CREATE DATABASE.
Never point it at production. Each test owns a unique database and drops only that name.
"""
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.backup import create_backup, restore_backup, verify_backup
from app.maintenance import maintenance_lock
from app.migrations import MigrationError, check, status, upgrade
from app.migrations.baseline import metadata, validate
from app.models import (
    AppConfig, Base, Conversation, DocChunk, Document, Message, PendingApproval,
    ToolPref, UsageLedger, User,
)


@pytest.fixture(params=['sqlite', 'postgresql'])
def engines(request, tmp_path):
    made = []
    admin = None
    if request.param == 'postgresql':
        url = os.environ.get('PHLOX_TEST_POSTGRES_URL')
        if not url:
            pytest.skip('Set PHLOX_TEST_POSTGRES_URL for disposable Postgres integration tests')
        admin = sa.create_engine(url, isolation_level='AUTOCOMMIT')

    def create():
        if admin is None:
            engine = sa.create_engine(f'sqlite:///{tmp_path / (uuid.uuid4().hex + ".db")}')
            made.append((engine, None))
        else:
            name = 'phlox_wave4_' + uuid.uuid4().hex
            with admin.connect() as conn:
                conn.exec_driver_sql(f'CREATE DATABASE {name}')
            engine = sa.create_engine(admin.url.set(database=name))
            made.append((engine, name))
        return engine

    yield create
    for engine, name in reversed(made):
        engine.dispose()
        if name:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE {name} WITH (FORCE)')
    if admin:
        admin.dispose()


def populate(engine):
    with Session(engine) as db:
        db.add(User(id='owner', username='owner', password_hash='test-hash', department='Science'))
        db.add(Conversation(id='conversation', user_id='owner', title='Restored chat'))
        db.flush()
        # Populate historical schemas through their frozen shape, before new columns exist.
        db.execute(metadata().tables['messages'].insert().values(
            id='message', conversation_id='conversation', role='user', content='Keep this text',
            attachments=[{'type': 'image', 'idx': 0, 'ext': 'png'}], created_at=datetime.now(timezone.utc),
        ))
        db.execute(metadata().tables['documents'].insert().values(id='document', user_id='owner', filename='source.txt', status='ready', size_bytes=14, n_chunks=1, created_at=datetime.now(timezone.utc)))
        db.flush()
        db.execute(metadata().tables['doc_chunks'].insert().values(id='a'*32, document_id='document', ordinal=0, text='Source passage', embedding=[0.1, 0.2]))
        db.add(PendingApproval(id='approval', conversation_id='conversation', state={'version': 3}, status='claimed'))
        db.add(ToolPref(name='write_file', enabled=True, permission='ask'))
        db.add(AppConfig(section='pricing', value={'meter': {'input': 1, 'output': 2}}))
        db.add(UsageLedger(message_id='call:receipt', user_id='owner', conversation_id='conversation',
                           model='meter', cost_usd=0.25, input_tokens=10, output_tokens=5, total_tokens=15))
        db.commit()


def assert_content(engine):
    with Session(engine) as db:
        assert db.get(User, 'owner').department == 'Science'
        assert db.scalar(sa.select(Message.content).where(Message.id == 'message')) == 'Keep this text'
        assert db.scalar(sa.select(Document.user_id).where(Document.id == 'document')) == 'owner'
        assert db.scalar(sa.select(DocChunk.embedding).where(DocChunk.id == 'a'*32)) == [0.1, 0.2]
        assert db.get(ToolPref, 'write_file').permission == 'ask'
        assert db.get(AppConfig, 'pricing').value['meter']['output'] == 2
        assert db.query(UsageLedger).one().cost_usd == 0.25
        assert db.get(PendingApproval, 'approval').status == 'claimed'


def test_fresh_and_repeated_upgrade_match_models(engines):
    engine = engines()
    upgrade(engine)
    upgrade(engine)
    assert status(engine) == {'current': '0005_ingestion', 'head': '0005_ingestion'}
    assert check(engine)['compatible']
    with engine.connect() as conn:
        validate(conn, expected=Base.metadata)


def test_populated_legacy_upgrade_preserves_data_and_repairs_indexes(engines):
    engine = engines()
    old = metadata()
    old.tables['usage_ledger'].c.message_id.type = sa.String(32)
    old.create_all(engine)
    populate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('DROP INDEX ix_usage_ledger_turn_id')
        conn.exec_driver_sql('ALTER TABLE usage_ledger DROP COLUMN turn_id')
        conn.exec_driver_sql('ALTER TABLE usage_ledger DROP COLUMN rate_snapshot')
        conn.exec_driver_sql('ALTER TABLE messages DROP COLUMN usage')
    upgrade(engine)
    assert_content(engine)
    with engine.connect() as conn:
        validate(conn, expected=Base.metadata)
    upgrade(engine)
    assert_content(engine)


@pytest.mark.parametrize('damage', ['extra_column', 'missing_column', 'wrong_type', 'unknown_revision'])
def test_schema_drift_fails_without_stamping_or_changes(engines, damage):
    engine = engines()
    metadata().create_all(engine)
    with engine.begin() as conn:
        if damage == 'extra_column':
            conn.exec_driver_sql('ALTER TABLE messages ADD COLUMN surprising TEXT')
        elif damage == 'missing_column':
            conn.exec_driver_sql('ALTER TABLE messages DROP COLUMN role')
        elif damage == 'wrong_type':
            conn.exec_driver_sql('ALTER TABLE messages DROP COLUMN role')
            conn.exec_driver_sql('ALTER TABLE messages ADD COLUMN role INTEGER NOT NULL')
        else:
            conn.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
            conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('future_revision')")
    with pytest.raises(MigrationError):
        upgrade(engine)
    with pytest.raises(MigrationError):
        check(engine)
    with engine.connect() as conn:
        if damage == 'unknown_revision':
            assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == 'future_revision'
        else:
            assert not sa.inspect(conn).has_table('alembic_version')


def test_migration_failure_rolls_back_ddl_and_revision(engines, monkeypatch):
    from app.migrations import baseline
    engine = engines()
    original = baseline.adopt

    def broken(conn):
        original(conn)
        raise RuntimeError('injected failure after DDL')

    monkeypatch.setattr(baseline, 'adopt', broken)
    with pytest.raises(MigrationError):
        upgrade(engine)
    with engine.connect() as conn:
        assert sa.inspect(conn).get_table_names() == []
    monkeypatch.setattr(baseline, 'adopt', original)
    upgrade(engine)


def test_concurrent_upgrade_serializes(engines):
    engine = engines()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: upgrade(engine), range(2)))
    assert status(engine)['current'] == '0005_ingestion'


def test_different_databases_do_not_share_alembic_context(engines):
    first, second = engines(), engines()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(upgrade, (first, second)))
    assert check(first)['compatible'] and check(second)['compatible']


def test_backup_restore_populated_instance(engines, tmp_path, monkeypatch):
    engine = engines()
    # Back up an unstamped prior release; restore must adopt and upgrade automatically.
    old = metadata()
    old.tables['usage_ledger'].c.message_id.type = sa.String(32)
    old.create_all(engine)
    populate(engine)
    data = tmp_path / 'data'
    files = {'uploads/document_source.txt': b'Source passage', 'attachments/message/0.png': b'image bytes',
             'workspaces/conversation/report.txt': b'Restored output'}
    for name, content in files.items():
        file = data / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    workspace = data / 'workspaces/conversation'
    (workspace / 'empty-directory').mkdir()
    subprocess.run(['git', 'init', str(workspace)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(workspace), 'add', 'report.txt'], check=True)
    subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Restore Test',
                    '-c', 'user.email=restore@example.invalid', '-c', 'commit.gpgsign=false',
                    'commit', '-m', 'Checkpoint fixture'], check=True, capture_output=True)
    checkpoint = subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', 'HEAD'])
    (data / 'qdrant').mkdir()
    (data / 'qdrant' / 'cache').write_text('derived')
    config = tmp_path / 'seed.yml'
    config.write_text('auth:\n  jwt_secret: keep-this-test-only-secret\nvector_store:\n  url: http://original-vector-server\n')
    monkeypatch.setenv('PHLOX_JWT_SECRET', 'never-copy-env-value')
    bundle = tmp_path / 'bundle'
    pg_bin = os.environ.get('PHLOX_TEST_PG_BIN_DIR')
    create_backup(engine, data, config, bundle, stopped=True, pg_bin_dir=pg_bin)
    manifest = verify_backup(bundle)
    assert 'never-copy-env-value' not in (bundle / 'manifest.json').read_text()
    assert 'PHLOX_JWT_SECRET' in manifest['secret_environment_names']
    assert not (bundle / 'data/qdrant').exists()
    target = tmp_path / 'restored'
    pg_target = engines() if engine.dialect.name == 'postgresql' else None
    url = pg_target.url if pg_target is not None else None
    restore_backup(bundle, target, database_url=url, stopped=True, pg_bin_dir=pg_bin)
    restored = pg_target or sa.create_engine(f'sqlite:///{target / "data/phlox.db"}')
    try:
        assert_content(restored)
        assert status(restored)['current'] == '0005_ingestion'
        for name, content in files.items():
            assert (target / 'data' / name).read_bytes() == content
        restored_workspace = target / 'data/workspaces/conversation'
        assert (restored_workspace / 'empty-directory').is_dir()
        assert subprocess.check_output(['git', '-C', str(restored_workspace), 'rev-parse', 'HEAD']) == checkpoint
        assert subprocess.check_output(['git', '-C', str(restored_workspace), 'status', '--porcelain']) == b''
        assert 'keep-this-test-only-secret' in (target / 'config.original.yml').read_text()
        import yaml
        vector_config = yaml.safe_load((target / 'config.yml').read_text())['vector_store']
        assert vector_config['url'] is None
        assert vector_config['path'] == str(target / 'data/qdrant')
        # Rebuild the derived index without model calls; ownership still filters retrieval.
        from app.rag.store import QdrantVectorStore
        from app.rag.retrieve import reindex_all
        store = QdrantVectorStore(vector_config)
        monkeypatch.setattr('app.rag.retrieve.get_vector_store', lambda: store)
        try:
            with Session(restored) as db:
                assert reindex_all(db) == 1
            assert store.search([0.1, 0.2], {}, 5, user_id='owner')[0]['payload']['text'] == 'Source passage'
            assert store.search([0.1, 0.2], {}, 5, user_id='other-owner') == []
        finally:
            store._client.close()
        # Existing destinations must be refused even when otherwise valid.
        with pytest.raises(ValueError):
            restore_backup(bundle, target, database_url=url, stopped=True, pg_bin_dir=pg_bin)
    finally:
        restored.dispose()


def test_live_lock_refuses_backup_and_releases(engines, tmp_path):
    engine = engines()
    upgrade(engine)
    data = tmp_path / 'data'
    config = tmp_path / 'config.yml'
    config.write_text('{}')
    with maintenance_lock(data, engine):
        with pytest.raises(RuntimeError):
            create_backup(engine, data, config, tmp_path / 'bundle', stopped=True)
        if engine.dialect.name == 'postgresql':
            with pytest.raises(RuntimeError):
                with maintenance_lock(tmp_path / 'other-data', engine):
                    pass
    with maintenance_lock(data, engine):
        pass
    assert not (tmp_path / 'bundle').exists()


def test_restore_rejects_nonempty_postgres_database(engines, tmp_path):
    source = engines()
    if source.dialect.name != 'postgresql':
        pytest.skip('Postgres-specific restore precondition')
    upgrade(source)
    config = tmp_path / 'config.yml'
    config.write_text('{}')
    bundle = tmp_path / 'bundle'
    pg_bin = os.environ.get('PHLOX_TEST_PG_BIN_DIR')
    create_backup(source, tmp_path / 'data', config, bundle, stopped=True, pg_bin_dir=pg_bin)
    destination = engines()
    with destination.begin() as conn:
        conn.exec_driver_sql('CREATE SEQUENCE keep_me')
    with pytest.raises(ValueError, match='empty dedicated'):
        restore_backup(bundle, tmp_path / 'restored', database_url=destination.url,
                       stopped=True, pg_bin_dir=pg_bin)
    with destination.connect() as conn:
        assert sa.inspect(conn).get_sequence_names() == ['keep_me']
        assert sa.inspect(conn).get_table_names() == []
    assert not (tmp_path / 'restored').exists()


def small_bundle(tmp_path):
    engine = sa.create_engine(f'sqlite:///{tmp_path / "source.db"}')
    upgrade(engine)
    config = tmp_path / 'config.yml'
    config.write_text('{}')
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'file.txt').write_text('original')
    bundle = tmp_path / 'bundle'
    create_backup(engine, data, config, bundle, stopped=True)
    engine.dispose()
    return bundle


@pytest.mark.parametrize('damage', ['checksum', 'extra', 'missing', 'symlink', 'traversal', 'format'])
def test_bad_bundle_refused_before_destination_creation(tmp_path, damage):
    bundle = small_bundle(tmp_path)
    if damage == 'checksum':
        (bundle / 'data/file.txt').write_text('tampered')
    elif damage == 'extra':
        (bundle / 'data/extra').write_text('unexpected')
    elif damage == 'missing':
        (bundle / 'database.sqlite').unlink()
    elif damage == 'symlink':
        (bundle / 'data/link').symlink_to(tmp_path / 'source.db')
    else:
        manifest = json.loads((bundle / 'manifest.json').read_text())
        if damage == 'traversal':
            manifest['files']['../escape'] = {'sha256': '', 'size': 0}
        else:
            manifest['format'] = 999
        (bundle / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore_backup(bundle, tmp_path / 'target', stopped=True)
    assert not (tmp_path / 'target').exists()


def test_offline_cli_roundtrip_and_redacted_error(tmp_path):
    bundle = small_bundle(tmp_path)
    env = {**os.environ, 'PHLOX_CONFIG': str(tmp_path / 'not-used.yml'),
           'PHLOX_DATA': str(tmp_path / 'not-used'), 'DATABASE_URL': 'do-not-connect://secret'}
    result = subprocess.run([sys.executable, '-m', 'app.ops', 'verify', str(bundle)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['verified']
    assert not (tmp_path / 'not-used').exists()
    result = subprocess.run([sys.executable, '-m', 'app.ops', 'restore', str(bundle),
                             '--output', str(tmp_path / 'restore'), '--stopped'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'secret' not in result.stdout + result.stderr


def test_stopped_confirmation_and_new_destination_required(tmp_path):
    bundle = small_bundle(tmp_path)
    with pytest.raises(ValueError, match='--stopped'):
        restore_backup(bundle, tmp_path / 'target')
    engine = sa.create_engine(f'sqlite:///{tmp_path / "source.db"}')
    try:
        with pytest.raises(ValueError, match='--stopped'):
            create_backup(engine, tmp_path / 'data', tmp_path / 'config.yml', tmp_path / 'new')
        with pytest.raises(ValueError, match='new and outside'):
            create_backup(engine, tmp_path / 'data', tmp_path / 'config.yml', bundle, stopped=True)
    finally:
        engine.dispose()


def test_backup_does_not_create_a_missing_source_database(tmp_path):
    source = tmp_path / 'missing.db'
    engine = sa.create_engine(f'sqlite:///{source}')
    config = tmp_path / 'config.yml'
    config.write_text('{}')
    try:
        with pytest.raises(ValueError, match='existing on-disk'):
            create_backup(engine, tmp_path / 'data', config, tmp_path / 'bundle', stopped=True)
        assert not source.exists()
        assert not (tmp_path / 'bundle').exists()
    finally:
        engine.dispose()


def test_restore_publication_does_not_replace_raced_destination(tmp_path, monkeypatch):
    from app import backup
    bundle = small_bundle(tmp_path)
    target = tmp_path / 'target'
    publish = backup._publish

    def race(temp, destination):
        destination.mkdir()
        (destination / 'unrelated').write_text('keep')
        publish(temp, destination)

    monkeypatch.setattr(backup, '_publish', race)
    with pytest.raises(FileExistsError):
        restore_backup(bundle, target, stopped=True)
    assert (target / 'unrelated').read_text() == 'keep'
    assert list(target.iterdir()) == [target / 'unrelated']


def test_lifespan_failure_releases_lock_and_readiness_checks_schema(client, monkeypatch):
    from app import main
    from app.config import DATA_DIR
    from app.database import ENGINE
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, 'validate_auth_startup', lambda: None)
    monkeypatch.setattr('app.sandbox.runner.validate_sandbox_startup', lambda: None)
    monkeypatch.setattr(main, 'init_db', lambda: (_ for _ in ()).throw(MigrationError('stop startup')))
    monkeypatch.setattr(main, 'register_builtin_tools', lambda _: pytest.fail('bootstrap continued after migration failure'))
    with pytest.raises(MigrationError), TestClient(main.app):
        pass
    with maintenance_lock(DATA_DIR, ENGINE):
        pass
    monkeypatch.setattr('app.migrations.status', lambda _: {'current': None, 'head': '0005_ingestion'})
    response = client.get('/api/readiness')
    assert response.status_code == 503 and response.json()['database']['ready'] is False


@pytest.mark.parametrize('stamped', [False, True])
def test_legacy_receipt_width_preserves_rows_and_unique_constraint(engines, stamped):
    engine = engines()
    old = metadata()
    ledger = old.tables['usage_ledger']
    ledger.c.message_id.type = sa.String(32)
    old.create_all(engine)
    populate(engine)
    # SQLite can already contain IDs longer than its declared VARCHAR(32).
    receipt = 'chatcmpl-' + 'b'*32 if engine.dialect.name == 'sqlite' else 'old-receipt'
    with Session(engine) as db:
        db.add(UsageLedger(message_id=receipt, cost_usd=1.5, usage_status='partial',
                           rate_snapshot={'input': 1}, usage_details={'input': 9}))
        db.add(UsageLedger(message_id=None, cost_usd=None))
        db.commit()
    with engine.begin() as conn:
        if stamped:
            conn.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)')
            conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('0001_wave3')")
        before = conn.execute(sa.select(ledger).order_by(ledger.c.id)).all()
    assert check(engine)['compatible']
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.select(ledger).order_by(ledger.c.id)).all() == before
        column = next(c for c in sa.inspect(conn).get_columns('usage_ledger') if c['name'] == 'message_id')
        assert column['type'].length == 64
        validate(conn, expected=Base.metadata)
    with Session(engine) as db:
        db.add(UsageLedger(message_id=receipt))
        with pytest.raises(sa.exc.IntegrityError):
            db.commit()
        db.rollback()
        db.add(UsageLedger(message_id='call:' + 'c'*32))
        db.commit()


def test_unknown_ledger_width_still_rejected(tmp_path):
    engine = sa.create_engine(f'sqlite:///{tmp_path / "bad-width.db"}')
    old = metadata()
    old.tables['usage_ledger'].c.message_id.type = sa.String(16)
    old.create_all(engine)
    try:
        with pytest.raises(MigrationError, match='Incompatible column usage_ledger.message_id'):
            check(engine)
        with pytest.raises(MigrationError):
            upgrade(engine)
        assert status(engine)['current'] is None
    finally:
        engine.dispose()


def test_receipt_width_failure_rolls_back_copy_and_revision(engines, monkeypatch):
    from app.migrations import baseline
    engine = engines()
    old = metadata()
    old.tables['usage_ledger'].c.message_id.type = sa.String(32)
    old.create_all(engine)
    populate(engine)
    original = baseline.validate

    def fail_after_revision(conn, **kwargs):
        original(conn, **kwargs)
        if kwargs.get('expected') is not None:
            raise RuntimeError('injected failure after column conversion')

    monkeypatch.setattr(baseline, 'validate', fail_after_revision)
    with pytest.raises(MigrationError):
        upgrade(engine)
    monkeypatch.setattr(baseline, 'validate', original)
    assert status(engine)['current'] is None
    with engine.connect() as conn:
        column = next(c for c in sa.inspect(conn).get_columns('usage_ledger') if c['name'] == 'message_id')
        assert column['type'].length == 32
    assert_content(engine)
    upgrade(engine)
    assert_content(engine)


def test_wave4_revision_can_be_checked_backed_up_and_upgraded(engines, tmp_path):
    engine = engines()
    metadata().create_all(engine)
    populate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)')
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('0002_ledger_width')")
    assert check(engine)['compatible']
    data = tmp_path / 'old-data'
    data.mkdir()
    config = tmp_path / 'old-config.yml'
    config.write_text('{}')
    create_backup(engine, data, config, tmp_path / 'old-bundle', stopped=True,
                  pg_bin_dir=os.environ.get('PHLOX_TEST_PG_BIN_DIR'))
    upgrade(engine)
    assert_content(engine)
    assert status(engine)['current'] == '0005_ingestion'


def test_run_evidence_survives_restore_without_replaying(engines, tmp_path):
    from sqlalchemy.orm import sessionmaker
    from app.models import Run, RunEvent, ToolExecution
    from app.runs import Worker, owned
    from fastapi import HTTPException

    engine = engines()
    upgrade(engine)
    populate(engine)
    with Session(engine) as db:
        db.add(Run(id='saved-run', user_id='owner', conversation_id='conversation',
                   active_conversation_id='conversation', request_key='saved-key', request_hash='a'*64,
                   payload={'pending_id': 'approval'}, status='running', pending_id='approval', last_seq=1))
        db.flush()
        db.add(RunEvent(run_id='saved-run', seq=1, data={'type': 'token', 'content': 'Private progress'}))
        db.add(ToolExecution(run_id='saved-run', call_id='action', name='write_file'))
        db.commit()
    data = tmp_path / 'source'
    data.mkdir()
    config = tmp_path / 'seed.yml'
    config.write_text('{}')
    pg_bin = os.environ.get('PHLOX_TEST_PG_BIN_DIR')
    bundle = tmp_path / 'bundle'
    create_backup(engine, data, config, bundle, stopped=True, pg_bin_dir=pg_bin)
    pg_target = engines() if engine.dialect.name == 'postgresql' else None
    target = tmp_path / 'restored'
    restore_backup(bundle, target, database_url=pg_target.url if pg_target is not None else None,
                   stopped=True, pg_bin_dir=pg_bin)
    restored = pg_target or sa.create_engine(f'sqlite:///{target / "data/phlox.db"}')
    try:
        with Session(restored) as db:
            assert owned(db, 'saved-run', 'owner').status == 'running'
            with pytest.raises(HTTPException) as error:
                owned(db, 'saved-run', 'other-admin')
            assert error.value.status_code == 404
            assert db.get(RunEvent, ('saved-run', 1)).data['content'] == 'Private progress'
        worker = Worker(sessionmaker(bind=restored))
        worker.recover()
        assert not worker.step()
        with Session(restored) as db:
            assert db.get(Run, 'saved-run').status == 'interrupted'
            assert db.query(ToolExecution).one().status == 'outcome_unknown'
            assert db.get(PendingApproval, 'approval').status == 'interrupted'
            db.delete(db.get(Conversation, 'conversation'))
            db.commit()
            assert not db.query(Run).count() and not db.query(RunEvent).count()
            assert not db.query(ToolExecution).count()
            assert db.query(UsageLedger).count() == 1
    finally:
        if pg_target is None:
            restored.dispose()


def test_worker_persists_execution_and_accounting_on_both_engines(engines, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy.orm import sessionmaker
    from app import runs
    from app.agent.registry import ToolRegistry
    from app.models import Run, RunEvent
    from app.providers.base import StreamDelta
    from app.schemas import ChatRequest

    engine = engines()
    upgrade(engine)
    factory = sessionmaker(bind=engine)
    worker = runs.Worker(factory)
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: True)
    monkeypatch.setattr('app.model_calls.SessionLocal', factory)
    monkeypatch.setattr('app.routers.chat.REGISTRY', ToolRegistry())

    class Provider:
        model = 'run-test-model'
        supports_tools = True

        def stream(self, *args):
            yield StreamDelta(type='usage', usage={'input': 2, 'output': 3, 'total': 5})
            yield StreamDelta(type='text', text='Durable answer')
            yield StreamDelta(type='done')

    monkeypatch.setattr('app.routers.chat.build_provider', lambda *args: Provider())
    with factory() as db:
        user = User(id='run-owner', username='runner', is_active=True, must_change_password=False)
        db.add(user)
        db.commit()
        run_id = runs.create(db, user, ChatRequest(message='hello'), 'key').id
    assert worker.step()
    with factory() as db:
        row = db.get(Run, run_id)
        assert row.status == 'completed'
        assert db.get(Message, row.message_id).content == 'Durable answer'
        assert db.query(RunEvent).filter_by(run_id=run_id).count() >= 3
        assert db.query(UsageLedger).filter_by(turn_id=run_id).one().total_tokens == 5


def test_wave5_upgrade_and_source_backup_restore(engines, tmp_path):
    from app.migrations import expected_metadata
    from app.sources import capture, inspect_source
    from app.models import Source, SourceUse

    engine = engines()
    expected_metadata('0003_runs').create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('0003_runs')")
    populate(engine)
    assert check(engine)['compatible']
    data = tmp_path / 'data'
    data.mkdir()
    config = tmp_path / 'config.yml'
    config.write_text('{}')
    pg_bin = os.environ.get('PHLOX_TEST_PG_BIN_DIR')
    create_backup(engine, data, config, tmp_path / 'before', stopped=True, pg_bin_dir=pg_bin)
    upgrade(engine)
    assert_content(engine)
    with Session(engine) as db:
        assert db.get(Message, 'message').citations is None
        ref, _ = capture(db, conversation_id='conversation', user_id='owner', turn_id='source-turn',
                         document_id='document', chunk_id='a'*32, query='find passage')
        db.get(Message, 'message').citations = [ref]
        db.commit()
    bundle = tmp_path / 'after'
    create_backup(engine, data, config, bundle, stopped=True, pg_bin_dir=pg_bin)
    target = tmp_path / 'restored'
    pg_target = engines() if engine.dialect.name == 'postgresql' else None
    restore_backup(bundle, target, database_url=pg_target.url if pg_target is not None else None,
                   stopped=True, pg_bin_dir=pg_bin)
    restored = pg_target or sa.create_engine(f'sqlite:///{target / "data/phlox.db"}')
    try:
        assert_content(restored)
        with Session(restored) as db:
            conv = db.get(Conversation, 'conversation')
            assert db.get(Message, 'message').citations == [ref]
            assert inspect_source(db, conv, ref['source_id'])['excerpt'] == 'Source passage'
            assert db.get(SourceUse, ('source-turn', ref['source_id'])).query == 'find passage'
            db.delete(db.get(Document, 'document'))
            db.commit()
            assert not inspect_source(db, conv, ref['source_id'])['available']
            assert db.get(Source, ref['source_id']).excerpt is None
            assert db.get(SourceUse, ('source-turn', ref['source_id'])).query == ''
    finally:
        restored.dispose()


def test_wave6_sources_upgrade_and_ingestion_metadata_restore(engines, tmp_path):
    import hashlib
    from datetime import timedelta
    from app.migrations import expected_metadata
    from app.models import Source, SourceUse
    from app.sources import capture
    engine = engines()
    expected_metadata('0004_sources').create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('0004_sources')")
    populate(engine)
    digest = hashlib.sha256(b'Source passage').hexdigest()
    fingerprint = hashlib.sha256(json.dumps(['document', 0, digest, 'Source passage']).encode()).hexdigest()
    with Session(engine) as db:
        source = Source(conversation_id='conversation', number=1, fingerprint=fingerprint,
                        document_id='document', chunk_id='a'*32, title='source.txt', excerpt='Source passage',
                        content_hash=digest, location={'chunk': 0, 'start': 0, 'end': 14, 'truncated': False},
                        expires_at=datetime.now(timezone.utc) + timedelta(days=30))
        db.add(source)
        db.flush()
        source_id = source.id
        db.add(SourceUse(turn_id='old-turn', source_id=source_id))
        db.commit()
    assert check(engine)['compatible']
    config = tmp_path / 'config.yml'
    config.write_text('{}')
    data = tmp_path / 'data'
    data.mkdir()
    pg_bin = os.environ.get('PHLOX_TEST_PG_BIN_DIR')
    create_backup(engine, data, config, tmp_path / 'before', stopped=True, pg_bin_dir=pg_bin)
    upgrade(engine)
    assert_content(engine)
    with Session(engine) as db:
        assert db.get(Document, 'document').ingestion is None
        assert db.get(DocChunk, 'a'*32).provenance is None
        ref, _ = capture(db, conversation_id='conversation', user_id='owner', turn_id='new-turn',
                         document_id='document', chunk_id='a'*32)
        assert ref['source_id'] == source_id  # Legacy fingerprint remains stable.
        db.get(Document, 'document').ingestion = {'stage': 'interrupted', 'attempt': 2}
        db.get(DocChunk, 'a'*32).provenance = {'page': 2, 'parser_version': '2'}
        db.get(DocChunk, 'a'*32).embedding_identity = {'fingerprint': 'versioned-test', 'dimensions': 2}
        db.commit()
    bundle = tmp_path / 'after'
    create_backup(engine, data, config, bundle, stopped=True, pg_bin_dir=pg_bin)
    target = tmp_path / 'restored'
    pg_target = engines() if engine.dialect.name == 'postgresql' else None
    restore_backup(bundle, target, database_url=pg_target.url if pg_target is not None else None,
                   stopped=True, pg_bin_dir=pg_bin)
    restored = pg_target or sa.create_engine(f'sqlite:///{target / "data/phlox.db"}')
    try:
        assert_content(restored)
        with Session(restored) as db:
            assert db.get(Document, 'document').ingestion['attempt'] == 2
            assert db.get(DocChunk, 'a'*32).provenance['page'] == 2
            assert db.get(DocChunk, 'a'*32).embedding_identity['dimensions'] == 2
            assert db.get(Source, source_id).excerpt == 'Source passage'
    finally:
        restored.dispose()
