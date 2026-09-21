"""Selections must survive the settings API and reach chat, beyond static catalogs."""
import pytest

from app import app_config
from app.models import AppConfig, Setting
from app.providers import discovery
from app.runtime_settings import get_settings, update_settings


@pytest.fixture(autouse=True)
def isolated_settings(db):
    saved = [(row.key, row.value) for row in db.query(Setting).all()]
    db.query(Setting).delete()
    db.commit()
    app_config.set_section('profiles', {
        'test': {'type': 'openai', 'model': 'default', 'models': ['configured'],
                 'model_discovery': 'automatic'},
        'other': {'type': 'openai', 'model': 'other-default'},
    })
    with discovery._CONDITION:
        discovery._CACHE.clear()
    yield
    db.query(Setting).delete()
    db.add_all(Setting(key=key, value=value) for key, value in saved)
    db.query(AppConfig).filter_by(section='profiles').delete()
    db.commit()
    app_config.invalidate()
    with discovery._CONDITION:
        discovery._CACHE.clear()


@pytest.mark.parametrize('mode,models', [
    ('automatic', ['configured']), ('automatic', []), ('manual', ['configured']),
])
def test_selected_model_survives_settings_reads_and_catalog_changes(client, monkeypatch, mode, models):
    app_config.set_section('profiles', {'test': {
        'type': 'openai', 'model': 'default', 'models': models, 'model_discovery': mode,
    }})
    selected = 'vendor/new-model:latest'
    monkeypatch.setattr(discovery, 'discover', lambda cfg: ([discovery.item(selected)], False, 'openai'))
    if mode == 'automatic':
        assert selected in client.get('/api/providers/test/models?refresh=true').json()['models']
    response = client.patch('/api/settings', json={'model': selected})
    assert response.status_code == 200
    assert response.json()['model'] == selected
    assert client.get('/api/settings').json()['model'] == selected
    assert client.patch('/api/settings', json={'temperature': 0.3}).json()['model'] == selected

    # Catalog availability and administrator defaults cannot overwrite an explicit choice.
    app_config.set_section('profiles', {'test': {
        'type': 'openai', 'model': 'changed-default', 'model_discovery': mode,
    }})
    def unavailable(cfg):
        raise RuntimeError('offline')
    monkeypatch.setattr(discovery, 'discover', unavailable)
    client.get('/api/providers/test/models?refresh=true')
    assert client.get('/api/settings').json()['model'] == selected


def test_profile_switch_defaults_and_explicit_model(client):
    client.patch('/api/settings', json={'model': 'custom'})
    assert client.patch('/api/settings', json={'active_profile': 'test'}).json()['model'] == 'custom'
    assert client.patch('/api/settings', json={'active_profile': 'other'}).json()['model'] == 'other-default'
    response = client.patch('/api/settings', json={'active_profile': 'test', 'model': 'new/custom'})
    assert response.json()['model'] == 'new/custom'
    assert client.patch('/api/settings', json={'model': ''}).json()['model'] == 'default'
    assert client.get('/api/settings').json()['model'] == 'default'


def test_profile_without_saved_model_uses_its_own_default(db):
    db.add(Setting(key='fresh:active_profile', value='other'))
    db.commit()
    assert get_settings(db, 'fresh')['model'] == 'other-default'


def test_model_choices_remain_per_user(db):
    update_settings(db, {'active_profile': 'other', 'model': 'private/custom'}, 'alice')
    assert get_settings(db, 'alice')['model'] == 'private/custom'
    assert get_settings(db, 'bob')['model'] == 'default'


def test_chat_invokes_selected_discovered_model(client, monkeypatch):
    from app.providers.base import StreamDelta
    from app.routers import chat

    selected = 'vendor/discovered-model'
    seen = []

    class Provider:
        model = selected
        supports_tools = False

        def stream(self, messages, tools, params):
            yield StreamDelta(type='text', text='Selected model replied.')
            yield StreamDelta(type='done', stop_reason='stop')

    def build(profile, model=None):
        seen.append((profile, model))
        return Provider()

    monkeypatch.setattr(chat, 'build_provider', build)
    monkeypatch.setattr(chat, '_build_fallback', lambda profile: None)
    client.patch('/api/settings', json={'model': selected})
    response = client.post('/api/chat', json={'message': 'Hello'})
    assert response.status_code == 200
    assert 'Selected model replied.' in response.text
    assert seen == [('test', selected)]
