"""Frozen Wave-3 schema and the only supported pre-Alembic adoption rules.

Do not edit schema_v1.json for new features: add an Alembic revision instead.
"""
import json
from pathlib import Path

import sqlalchemy as sa

SCHEMA = json.loads(Path(__file__).with_name('schema_v1.json').read_text())


def metadata():
    result = sa.MetaData()
    for name, spec in SCHEMA['tables'].items():
        columns = []
        for c in spec['columns']:
            kind, _, length = c['type'].partition('(')
            typ = {'VARCHAR': sa.String, 'TEXT': sa.Text, 'INTEGER': sa.Integer,
                   'FLOAT': sa.Float, 'BOOLEAN': sa.Boolean, 'DATETIME': sa.DateTime,
                   'JSON': sa.JSON}[kind]
            typ = typ(int(length.rstrip(')'))) if length else typ()
            columns.append(sa.Column(c['name'], typ, nullable=c['nullable'], primary_key=c['primary_key']))
        constraints = [sa.UniqueConstraint(*u) for u in spec['unique']]
        constraints += [sa.ForeignKeyConstraint(f['columns'], f['target'], ondelete=f['ondelete'])
                        for f in spec['foreign_keys']]
        table = sa.Table(name, result, *columns, *constraints)
        for i in spec['indexes']:
            sa.Index(i['name'], *(table.c[n] for n in i['columns']), unique=i['unique'])
    return result


def validate(conn, *, legacy=False, expected=None, allow_legacy_ledger_width=False):
    """Check known schema shape before adoption/stamping; never silently accept drift."""
    inspector = sa.inspect(conn)
    actual_tables = set(inspector.get_table_names()) - {'alembic_version'}
    expected = metadata() if expected is None else expected
    if actual_tables - set(expected.tables):
        raise ValueError('Unrecognized tables: ' + ', '.join(sorted(actual_tables - set(expected.tables))))
    if not legacy and actual_tables != set(expected.tables):
        raise ValueError('Missing application tables')
    for name in sorted(actual_tables):
        table = expected.tables[name]
        cols = {c['name']: c for c in inspector.get_columns(name)}
        allowed_missing = set(SCHEMA['legacy_additions'].get(name, {})) if legacy else set()
        if set(cols) - set(table.c.keys()) or set(table.c.keys()) - set(cols) - allowed_missing:
            raise ValueError(f'Unexpected or missing columns in {name}')
        for c in table.c:
            if c.name not in cols:
                continue
            old = cols[c.name]
            legacy_width = ((legacy or allow_legacy_ledger_width)
                            and name == 'usage_ledger' and c.name == 'message_id'
                            and isinstance(old['type'], sa.String) and old['type'].length == 32
                            and isinstance(c.type, sa.String) and c.type.length == 64)
            if (old['type']._type_affinity is not c.type._type_affinity
                    or (getattr(old['type'], 'length', None) != getattr(c.type, 'length', None)
                        and not legacy_width)
                    or (not c.primary_key and old['nullable'] != c.nullable)):
                raise ValueError(f'Incompatible column {name}.{c.name}')
        pk = inspector.get_pk_constraint(name).get('constrained_columns') or []
        if pk != list(table.primary_key.columns.keys()):
            raise ValueError(f'Incompatible primary key in {name}')
        uniques = {tuple(c['column_names']) for c in inspector.get_unique_constraints(name)}
        uniques |= {tuple(i['column_names']) for i in inspector.get_indexes(name) if i['unique']}
        expected_unique = {tuple(c.columns.keys()) for c in table.constraints if isinstance(c, sa.UniqueConstraint)}
        expected_unique |= {tuple(i.columns.keys()) for i in table.indexes if i.unique}
        if uniques != expected_unique:
            raise ValueError(f'Incompatible unique constraints in {name}')
        fks = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns']),
                (f.get('options') or {}).get('ondelete')) for f in inspector.get_foreign_keys(name)}
        wanted = {(tuple(f.column_keys), f.referred_table.name,
                   tuple(e.column.name for e in f.elements), f.ondelete) for f in table.foreign_key_constraints}
        if fks != wanted:
            raise ValueError(f'Incompatible foreign keys in {name}')
        indexes = {i['name']: (tuple(i['column_names']), i['unique']) for i in inspector.get_indexes(name)}
        for i in table.indexes:
            if i.name in indexes and indexes[i.name] != (tuple(i.columns.keys()), i.unique):
                raise ValueError(f'Incompatible index {i.name}')
            if not legacy and i.name not in indexes:
                raise ValueError(f'Missing index {i.name}')


def add_legacy_columns(conn):
    inspector = sa.inspect(conn)
    for table, additions in SCHEMA['legacy_additions'].items():
        if not inspector.has_table(table):
            continue
        existing = {c['name'] for c in inspector.get_columns(table)}
        for name, typ in additions.items():
            if name not in existing:
                conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {typ}')


def adopt(conn):
    validate(conn, legacy=True)
    add_legacy_columns(conn)
    frozen = metadata()
    frozen.create_all(conn)
    for table in frozen.tables.values():
        for index in table.indexes:
            index.create(conn, checkfirst=True)
    # Compatibility correction for historical ledger tables: revision 0002 widens this
    # single known column. Keep all other shape/index checks strict at the baseline seam.
    validate(conn, allow_legacy_ledger_width=True)
