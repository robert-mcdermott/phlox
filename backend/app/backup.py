"""Offline, verified backup bundles; restore only into new locations/databases.

Bundles contain secrets and private data. Only restore trusted operator-created bundles.
No archive extraction, overwrite switch, automatic external connections, or secret logging.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import sqlalchemy as sa
import yaml

from app.maintenance import maintenance_lock
from app.migrations import known_revision, status, upgrade
from app.migrations.baseline import validate

FORMAT = 1


def _hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _files(root):
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('Backup trees must contain only regular files/directories (no symlinks)')
        if path.is_file():
            yield path


def _copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, target)
    target.chmod(0o600 | (source.stat().st_mode & 0o100))


def _publish(temp, destination):
    # Claim a new name exclusively; rename must never replace a pre-existing directory.
    if os.name == 'nt':
        # Windows rename already refuses any existing destination, including empty dirs.
        temp.rename(destination)
        return
    destination.mkdir(mode=0o700)
    try:
        temp.rename(destination)
    except BaseException:
        try:
            destination.rmdir()
        except OSError:
            pass
        raise


def _pg(engine, program, *, output=None, source=None, bin_dir=None):
    url = engine.url
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    for key, value in {'PGHOST': url.host, 'PGPORT': url.port, 'PGUSER': url.username,
                       'PGPASSWORD': url.password, 'PGDATABASE': url.database}.items():
        if value is not None:
            env[key] = str(value)
    options = {'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT', 'sslcert': 'PGSSLCERT',
               'sslkey': 'PGSSLKEY', 'connect_timeout': 'PGCONNECT_TIMEOUT'}
    if set(url.query) - set(options):
        raise ValueError('Unsupported Postgres URL options for backup/restore')
    env.update({options[k]: v for k, v in url.query.items()})
    executable = str(Path(bin_dir) / program) if bin_dir else program
    args = ([executable, '--format=custom', '--no-owner', '--no-privileges'] if program == 'pg_dump'
            else [executable, '--dbname', url.database or '', '--no-owner', '--no-privileges',
                  '--single-transaction', '--exit-on-error'])
    # Connection secrets go through the subprocess environment, never argv or manifest.
    with (output.open('wb') if output else source.open('rb')) as stream:
        result = subprocess.run(args, env=env, stdin=stream if source else subprocess.DEVNULL,
                                stdout=stream if output else subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=3600)
    if result.returncode:
        raise RuntimeError(f'{program} failed (exit {result.returncode}); check client/server versions and credentials')


def create_backup(engine, data_dir, config_path, destination, *, stopped=False, pg_bin_dir=None):
    if not stopped:
        raise ValueError('Stop all Phlox processes and external writers, then pass --stopped')
    data_dir, config_path, destination = (Path(p).resolve() for p in (data_dir, config_path, destination))
    if destination.exists() or destination.is_relative_to(data_dir):
        raise ValueError('Backup destination must be new and outside the data directory')
    if not config_path.is_file():
        raise ValueError('A readable seed config file is required')
    backend = engine.dialect.name
    if backend not in {'sqlite', 'postgresql'}:
        raise ValueError('Only SQLite and Postgres are supported')
    if backend == 'sqlite' and (not engine.url.database or engine.url.database == ':memory:'
                               or not Path(engine.url.database).is_file()):
        raise ValueError('Backup requires an existing on-disk SQLite database')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.phlox-backup-', dir=destination.parent))
    try:
        with maintenance_lock(data_dir, engine):
            revision = status(engine)
            if not known_revision(revision['current']):
                raise ValueError('Use the matching Phlox release to back up this schema revision')
            with engine.connect() as conn:
                from app.models import Base
                validate(conn, legacy=revision['current'] is None,
                         expected=Base.metadata if revision['current'] else None,
                         allow_legacy_ledger_width=revision['current'] == '0001_wave3')
            cfg = yaml.safe_load(config_path.read_text()) or {}
            vector = cfg.get('vector_store') or {}
            qdrant = Path(vector.get('path') or data_dir / 'qdrant')
            if not qdrant.is_absolute():
                # Match config.get_vector_store_config's backend-relative path convention.
                qdrant = Path(__file__).resolve().parent.parent / qdrant
            qdrant = qdrant.resolve()
            sql_path = Path(engine.url.database).resolve() if backend == 'sqlite' else None
            skip = {sql_path, Path(str(sql_path)+'-wal'), Path(str(sql_path)+'-shm'),
                    data_dir / '.phlox-maintenance.lock'}
            (temp / 'data').mkdir(mode=0o700)
            for path in _files(data_dir):
                if path in skip or path.is_relative_to(qdrant):
                    continue
                _copy(path, temp / 'data' / path.relative_to(data_dir))
            for path in data_dir.rglob('*'):
                if path.is_dir() and not path.is_relative_to(qdrant):
                    (temp / 'data' / path.relative_to(data_dir)).mkdir(parents=True, exist_ok=True, mode=0o700)
            _copy(config_path, temp / 'config.yml')
            if backend == 'sqlite':
                dbfile = temp / 'database.sqlite'
                with sqlite3.connect(sql_path.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(dbfile) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('SQLite integrity check failed')
            else:
                dbfile = temp / 'database.dump'
                _pg(engine, 'pg_dump', output=dbfile, bin_dir=pg_bin_dir)
            dbfile.chmod(0o600)
            from app.branding import get_version
            manifest = {'format': FORMAT, 'app_version': get_version(display=False),
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'database': backend, 'revision': revision['current'],
                        'qdrant': 'excluded; rebuild from stored chunks and embeddings',
                        'directories': sorted(p.relative_to(temp).as_posix() for p in temp.rglob('*') if p.is_dir()),
                        'secret_environment_names': sorted(k for k in os.environ if
                            k.startswith(('PHLOX_', 'AWS_', 'AZURE_', 'OPENAI_')) or k == 'DATABASE_URL'),
                        'files': {p.relative_to(temp).as_posix(): {'sha256': _hash(p), 'size': p.stat().st_size}
                                  for p in _files(temp)}}
            (temp / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
            (temp / 'manifest.json').chmod(0o600)
            verify_backup(temp)
            _publish(temp, destination)
        return {'database': backend, 'revision': revision['current'], 'files': len(manifest['files'])}
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def verify_backup(bundle):
    bundle = Path(bundle).resolve()
    manifest_path = bundle / 'manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 16_000_000:
        raise ValueError('Invalid manifest')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != FORMAT or manifest.get('database') not in {'sqlite', 'postgresql'}:
        raise ValueError('Unsupported backup format')
    expected = manifest.get('files')
    if not isinstance(expected, dict):
        raise ValueError('Invalid file inventory')
    actual = {p.relative_to(bundle).as_posix() for p in _files(bundle)} - {'manifest.json'}
    if actual != set(expected):
        raise ValueError('Backup file inventory does not match manifest')
    required = {'config.yml', 'database.sqlite' if manifest['database'] == 'sqlite' else 'database.dump'}
    if not required <= actual:
        raise ValueError('Backup is missing its database or config')
    directories = manifest.get('directories')
    actual_dirs = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_dir()}
    if not isinstance(directories, list) or set(directories) != actual_dirs:
        raise ValueError('Backup directory inventory does not match manifest')
    for name in directories:
        rel = PurePosixPath(name)
        if name != 'data' and (not name.startswith('data/') or rel.is_absolute()
                              or '..' in rel.parts or '\\' in name or ':' in name):
            raise ValueError('Invalid backup directory path')
    for name, info in expected.items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name or not (
                name in required or name.startswith('data/')):
            raise ValueError('Invalid backup path')
        path = bundle / name
        if path.stat().st_size != info['size'] or _hash(path) != info['sha256']:
            raise ValueError('Backup checksum mismatch')
    return manifest


def restore_backup(bundle, destination, *, database_url=None, stopped=False, pg_bin_dir=None):
    if not stopped:
        raise ValueError('Stop all Phlox processes and external writers, then pass --stopped')
    bundle, destination = Path(bundle).resolve(), Path(destination).resolve()
    manifest = verify_backup(bundle)  # Verify before creating a target or touching a database.
    if destination.exists() or destination.is_relative_to(bundle):
        raise ValueError('Restore destination must be a new directory outside the bundle')
    if manifest['database'] == 'postgresql' and not database_url:
        raise ValueError('Postgres restore requires an explicit empty destination database')
    if manifest['database'] == 'sqlite' and database_url:
        raise ValueError('Cross-database conversion is not supported')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.phlox-restore-', dir=destination.parent))
    engine = None
    try:
        (temp / 'data').mkdir(mode=0o700)
        for name in manifest['directories']:
            (temp / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in manifest['files']:
            if name.startswith('data/'):
                _copy(bundle / name, temp / name)
        _copy(bundle / 'config.yml', temp / 'config.original.yml')
        cfg = yaml.safe_load((bundle / 'config.yml').read_text()) or {}
        cfg['vector_store'] = {**(cfg.get('vector_store') or {}), 'url': None,
                               'path': str(destination / 'data' / 'qdrant')}
        if manifest['database'] == 'sqlite':
            _copy(bundle / 'database.sqlite', temp / 'data' / 'phlox.db')
            engine = sa.create_engine(sa.URL.create('sqlite', database=str(temp / 'data' / 'phlox.db')))
            cfg['database'] = {'url': sa.URL.create('sqlite', database=str(destination / 'data' / 'phlox.db')).render_as_string(hide_password=False)}
        else:
            engine = sa.create_engine(database_url)
            if engine.dialect.name != 'postgresql':
                raise ValueError('Expected a Postgres destination URL')
            # Do not write new credentials into the generated config. Require explicit env at launch.
            cfg['database'] = {'url': 'postgresql+psycopg://RESTORE_REQUIRES_DATABASE_URL/unused'}
        with maintenance_lock(temp / 'data', engine):
            if manifest['database'] == 'postgresql':
                with engine.connect() as conn:
                    schemas = set(sa.inspect(conn).get_schema_names()) - {'information_schema', 'public'}
                    objects = conn.exec_driver_sql("""SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'
                        UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public' LIMIT 1""").first()
                    if objects or schemas:
                        raise ValueError('Postgres restore destination must be an empty dedicated database')
                _pg(engine, 'pg_restore', source=bundle / 'database.dump', bin_dir=pg_bin_dir)
            upgrade(engine)
            (temp / 'config.yml').write_text(yaml.safe_dump(cfg, sort_keys=False))
            (temp / 'config.yml').chmod(0o600)
        engine.dispose()
        _publish(temp, destination)
        return {'database': manifest['database'], 'revision': manifest['revision'], 'index': 'rebuild required'}
    finally:
        if engine is not None:
            engine.dispose()
        if temp.exists():
            shutil.rmtree(temp)
