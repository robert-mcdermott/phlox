"""Bounded, deterministic tabular analysis; no model-authored rows or executable expressions."""
from decimal import Decimal, localcontext

from app.api_dataset_formats import DatasetError, amount
from app.web_extract_worker import Number

MAX_COLUMNS = 64
MAX_GROUPS = 50


def flatten(record, prefix=''):
    out = {}
    for key, value in record.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        elif not isinstance(value, list) or all(v is None or isinstance(v, (str, bool)) for v in value):
            out[path] = value
    return out


def kind(value):
    if value is None:
        return 'missing'
    if isinstance(value, Number):
        return 'number'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, list):
        return 'list'
    return 'text'


def key(value):
    category = kind(value)
    if category == 'number':
        return category, amount(value)
    return category, value


def inspect(records):
    rows = [flatten(record) for record in records]
    fields = sorted({field for row in rows for field in row})
    if len(fields) > MAX_COLUMNS:
        raise DatasetError('Dataset has too many analyzable columns.')
    columns = []
    for field in fields:
        values = [row.get(field) for row in rows]
        columns.append({'field': field, 'types': sorted({kind(v) for v in values if v is not None}),
                        'missing_records': sum(v is None or v == [] or v == '' for v in values)})
    return rows, columns


def display(value):
    if value is None:
        return 'Not reported'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    return '"Not reported"' if value == 'Not reported' else str(value)


def scalar_match(value, op, wanted):
    if value is None:
        return False
    if op in {'minimum', 'maximum'}:
        if not isinstance(value, Number):
            raise DatasetError('Numeric filters require a numeric scalar column.')
        limit = amount(Number(wanted))
        return amount(value) >= limit if op == 'minimum' else amount(value) <= limit
    if op == 'contains':
        if type(value) is not str:
            raise DatasetError('Contains filters require text or a list of text values.')
        return wanted.casefold() in value.casefold()
    if isinstance(value, Number):
        return amount(value) == amount(Number(wanted))
    return display(value) == wanted


def analyze(records, sections, filters, check=lambda: None):
    rows, columns = inspect(records)
    available = {column['field']: column for column in columns}
    for rule in filters:
        if rule['field'] not in available:
            raise DatasetError('Unknown filter column. Inspect the dataset first.')
        types = set(available[rule['field']]['types'])
        if rule['op'] in {'minimum', 'maximum'} and not types <= {'number'}:
            raise DatasetError('Numeric filters require a numeric scalar column.')
        if rule['op'] in {'minimum', 'maximum'}:
            amount(Number(rule['value']))
    selected = []
    for row in rows:
        check()
        matches = True
        for rule in filters:
            value = row.get(rule['field'])
            values = value if isinstance(value, list) else [value]
            if not any(scalar_match(v, rule['op'], rule['value']) for v in values):
                matches = False
                break
        if matches:
            selected.append(row)
    results = []
    with localcontext() as context:
        context.prec = 512  # Exact for existing bounded numeric inputs and source counts.
        for section in sections:
            check()
            field = section.get('group_by')
            metric = section.get('metric', 'count')
            numeric = section.get('value_field')
            if field is not None and field not in available:
                raise DatasetError('Unknown grouping column. Inspect the dataset first.')
            multi = field is not None and 'list' in available[field]['types']
            if metric == 'sum':
                if numeric not in available or not set(available[numeric]['types']) <= {'number'}:
                    raise DatasetError('Sum requires value_field naming a numeric scalar column.')
                if multi:
                    raise DatasetError('Sum over a multi-valued grouping is unsupported; it could double count values. Use count or a scalar group.')
            elif numeric is not None:
                raise DatasetError('value_field is only valid with metric=sum.')
            groups = {}
            for row in selected:
                check()
                value = row.get(field) if field else 'All selected records'
                values = (value or [None]) if isinstance(value, list) else [value]
                values = [None if v == '' else v for v in values]
                unique = {key(v): v for v in values}
                for identity, group_value in unique.items():
                    if identity not in groups:
                        if len(groups) >= MAX_GROUPS:
                            raise DatasetError('More than 50 groups. Filter the dataset or choose a lower-cardinality column.')
                        groups[identity] = {'group': group_value, 'record_count': 0, 'known_count': 0,
                                            'missing_count': 0, 'total': Decimal(0)}
                    group = groups[identity]
                    group['record_count'] += 1
                    if metric == 'sum':
                        number = amount(row.get(numeric))
                        group['missing_count' if number is None else 'known_count'] += 1
                        if number is not None:
                            group['total'] += number
            output = []
            for group in groups.values():
                total = group.pop('total')
                group['value'] = (group['record_count'] if metric == 'count' else
                                  Number(format(total, 'f')) if group['known_count'] else None)
                output.append(group)
            output.sort(key=lambda group: (group['value'] is None,
                -Decimal(str(group['value'])) if group['value'] is not None else Decimal(0),
                display(group['group'])))
            results.append({'title': section['title'], 'group_by': field, 'metric': metric,
                            'value_field': numeric, 'multi_valued_groups': multi,
                            'membership_count': sum(g['record_count'] for g in output), 'groups': output})
    return {'input_records': len(rows), 'selected_records': len(selected), 'excluded_records': len(rows) - len(selected),
            'filters': filters, 'columns': columns, 'sections': results,
            'method': 'Counts are distinct records within each group. Multi-valued groups can overlap. '
                      'Missing/empty categories are Not reported. Sums include known numeric values only; entirely missing sums are null. '
                      'Filters are ANDed; list matches use any element. Contains is case-insensitive; equals is exact.'}
