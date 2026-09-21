"""Workspace glob behavior, including zero-directory ** matches."""
from types import SimpleNamespace

import pytest

from app.agent.tools.fs import GlobSearch, GrepSearch, _glob_match


@pytest.mark.parametrize('path,pattern,matched', [
    ('api-dataset-a/records.csv', '**/api-dataset-*/records.csv', True),
    ('nested/api-dataset-a/records.csv', '**/api-dataset-*/records.csv', True),
    ('records.csv', '**/*.csv', True),
    ('a/b/c.csv', 'a/**/c.csv', True),
    ('a/c.csv', 'a/**/c.csv', True),
    ('a/b/c.csv', 'a/*.csv', False),
    ('a/records.json', '**/*.csv', False),
    ('a/test1.py', './a/test[0-9].py', True),
])
def test_recursive_patterns(path, pattern, matched):
    assert _glob_match(path, pattern) is matched


def test_workspace_search_finds_root_and_nested_datasets_but_ignores_vendor_dirs(tmp_path, monkeypatch):
    paths = ['api-dataset-a/records.csv', 'nested/api-dataset-b/records.csv', '.git/api-dataset-hidden/records.csv']
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('year,amount\n2024,10\n')
    monkeypatch.setattr('app.agent.tools.fs.resolve_in_workspace', lambda *args: tmp_path)
    ctx = SimpleNamespace(conversation_id='fixture')
    found = GlobSearch().run(ctx, pattern='**/api-dataset-*/records.csv')
    assert found.content.splitlines() == paths[:2]
    grep = GrepSearch().run(ctx, pattern='2024', glob='**/api-dataset-*/records.csv')
    assert 'api-dataset-a/records.csv:2:' in grep.content
    assert 'nested/api-dataset-b/records.csv:2:' in grep.content
    assert '.git' not in grep.content
