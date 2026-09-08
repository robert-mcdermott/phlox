"""Cooperative exclusive lock for the supported single-process deployment.

The server holds this for its whole lifespan. Offline maintenance refuses an active
server; PostgreSQL also locks the database across differing local data paths.
"""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def maintenance_lock(data_dir, engine=None):
    path = Path(data_dir) / '.phlox-maintenance.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open('a+b')
    conn = None
    locked = False
    try:
        if __import__('os').name == 'nt':
            import msvcrt
            file.write(b'0')
            file.flush()
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('Phlox or another maintenance command is using this data directory') from None
        else:
            import fcntl
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError('Phlox or another maintenance command is using this data directory') from None
        locked = True
        if engine is not None and engine.dialect.name == 'postgresql':
            conn = engine.connect()
            if not conn.exec_driver_sql('SELECT pg_try_advisory_lock(1886154617)').scalar():
                raise RuntimeError('Phlox or another maintenance command is using this database')
        yield
    finally:
        if conn is not None:
            try:
                conn.exec_driver_sql('SELECT pg_advisory_unlock(1886154617)')
            finally:
                conn.close()
        if locked and __import__('os').name == 'nt':
            import msvcrt
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        file.close()
