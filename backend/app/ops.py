"""Operator CLI: uv run -m app.ops --help. Never prints config or connection secrets."""
import argparse
import json
import os


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    db = commands.add_parser('db', help='Inspect or upgrade the configured database')
    db.add_argument('action', choices=['status', 'check', 'upgrade'])
    backup = commands.add_parser('backup', help='Create a verified offline bundle')
    backup.add_argument('--output', required=True)
    backup.add_argument('--stopped', action='store_true', help='Confirm all app/external writers are stopped')
    backup.add_argument('--pg-bin-dir')
    verify = commands.add_parser('verify', help='Check a bundle without connecting to a database')
    verify.add_argument('bundle')
    restore = commands.add_parser('restore', help='Restore a trusted bundle into a new directory')
    restore.add_argument('bundle')
    restore.add_argument('--output', required=True)
    restore.add_argument('--stopped', action='store_true')
    restore.add_argument('--database-url-env', default='PHLOX_RESTORE_DATABASE_URL')
    restore.add_argument('--pg-bin-dir')
    commands.add_parser('reindex', help='Offline rebuild from saved embeddings; uses configured vector target')
    args = parser.parse_args(argv)
    try:
        if args.command in {'verify', 'restore'}:
            from app.backup import restore_backup, verify_backup
            if args.command == 'verify':
                result = verify_backup(args.bundle)
                result = {'verified': True, 'database': result['database'], 'files': len(result['files'])}
            else:
                result = restore_backup(args.bundle, args.output, stopped=args.stopped,
                                        database_url=os.environ.get(args.database_url_env), pg_bin_dir=args.pg_bin_dir)
        else:
            from app.config import CONFIG_PATH, DATA_DIR
            from app.database import ENGINE, SessionLocal
            from app.maintenance import maintenance_lock
            from app.migrations import check, status, upgrade
            if args.command == 'backup':
                from app.backup import create_backup
                result = create_backup(ENGINE, DATA_DIR, CONFIG_PATH, args.output,
                                       stopped=args.stopped, pg_bin_dir=args.pg_bin_dir)
            elif args.command == 'db' and args.action in {'status', 'check'}:
                result = status(ENGINE) if args.action == 'status' else check(ENGINE)
            else:
                with maintenance_lock(DATA_DIR, ENGINE):
                    if args.command == 'db':
                        if args.action == 'upgrade':
                            upgrade(ENGINE)
                        result = status(ENGINE)
                    else:
                        from app.rag.retrieve import reindex_all
                        upgrade(ENGINE)
                        with SessionLocal() as session:
                            result = {'indexed': reindex_all(session)}
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Exceptions from DB drivers can contain secrets, SQL and data. Keep CLI errors redacted.
        from app.migrations import MigrationError
        detail = str(exc) if isinstance(exc, MigrationError) else (
            f'{args.command} failed ({type(exc).__name__}). Check paths, stopped writers, '
            'bundle integrity, schema compatibility, and credentials; see docs/BACKUP_RESTORE.md.')
        print(detail, file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
