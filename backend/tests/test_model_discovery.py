"""Catalog adapters, refresh/recovery, credential isolation and read-only admin preview."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app import app_config, config
from app.models import AppConfig
from app.providers import discovery as d


@pytest.fixture(autouse=True)
def clean(db, monkeypatch):
    db.query(AppConfig).filter_by(section='profiles').delete()
    db.commit()
    app_config.invalidate()
    with d._CONDITION:
        d._CACHE.clear()
    # Tests explicitly control cache expiry without waiting three seconds.
    monkeypatch.setattr(d, 'MIN_REFRESH_SECONDS', 0)
    yield
    db.query(AppConfig).filter_by(section='profiles').delete()
    db.commit()
    app_config.invalidate()
    with d._CONDITION:
        d._CACHE.clear()


def install(**patch):
    cfg = {'type': 'openai', 'endpoint': 'https://provider.test/v1', 'model': 'default', **patch}
    app_config.set_section('profiles', {'local': cfg})
    return cfg


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(d.httpx, 'Client', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))


def test_existing_curated_list_never_calls_provider(monkeypatch, client):
    install(models=['curated', 'default'])
    monkeypatch.setattr(d, 'discover', lambda cfg: pytest.fail('Curated list dispatched discovery'))
    body = client.get('/api/providers/local/models?refresh=true').json()
    assert body['mode'] == 'manual'
    assert body['models'] == ['curated', 'default']
    assert all(not row['discovered'] for row in body['items'])


def test_refresh_finds_added_models_and_preserves_custom_id(monkeypatch, client):
    install(models=['custom'], model_discovery='automatic')
    ids = ['one']
    calls = []
    def read(cfg):
        calls.append(True)
        return [d.item(value) for value in ids], False, 'openai'
    monkeypatch.setattr(d, 'discover', read)
    first = client.get('/api/providers/local/models').json()
    assert first['models'] == ['custom', 'default', 'one']
    ids.append('two')
    assert client.get('/api/providers/local/models').json()['models'] == first['models']
    fresh = client.get('/api/providers/local/models?refresh=true').json()
    assert fresh['models'] == ['custom', 'default', 'one', 'two']
    assert fresh['last_success_at'] and len(calls) == 2


def test_failure_retains_last_good_list_and_recovery_clears_error(monkeypatch, caplog):
    install(api_key='PRIVATE-KEY')
    monkeypatch.setattr(d, 'discover', lambda cfg: ([d.item('working')], False, 'openai'))
    good = d.catalog('local')
    def fail(cfg):
        raise RuntimeError('PRIVATE-KEY private response body')
    monkeypatch.setattr(d, 'discover', fail)
    stale = d.catalog('local', refresh=True)
    assert stale['models'] == good['models']
    assert stale['stale'] and stale['status'] == 'error'
    assert stale['last_success_at'] == good['last_success_at']
    assert 'PRIVATE' not in json.dumps(stale) + caplog.text
    monkeypatch.setattr(d, 'discover', lambda cfg: ([d.item('recovered')], False, 'openai'))
    recovered = d.catalog('local', refresh=True)
    assert recovered['models'] == ['default', 'recovered']
    assert not recovered['stale'] and recovered['error'] is None


@pytest.mark.parametrize('change', [{'api_key': 'new-secret'}, {'endpoint': 'https://other.test/v1'},
                                   {'discovery_api': 'ollama'}])
def test_cache_never_reuses_catalog_after_identity_change(monkeypatch, change):
    cfg = install(api_key='old-secret')
    calls = []
    monkeypatch.setattr(d, 'discover', lambda cfg: calls.append(cfg) or ([d.item('first')], False, 'openai'))
    d.catalog('local')
    install(**{**cfg, **change})
    monkeypatch.setattr(d, 'discover', lambda cfg: (_ for _ in ()).throw(httpx.ConnectError('secret')))
    result = d.catalog('local')
    assert 'first' not in result['models']
    assert not result['stale'] and result['status'] == 'error'
    assert 'old-secret' not in str(d._CACHE)


def test_coalesces_parallel_refreshes_and_bounds_cache(monkeypatch):
    install()
    monkeypatch.setattr(d, 'MIN_REFRESH_SECONDS', 3)
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def read(cfg):
        calls.append(True)
        entered.set()
        assert finish.wait(3)
        return [d.item('one')], False, 'openai'
    monkeypatch.setattr(d, 'discover', read)
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(d.catalog, 'local', refresh=True)
        assert entered.wait(2)
        two = pool.submit(d.catalog, 'local', refresh=True)
        finish.set()
        assert one.result()['models'] == two.result()['models']
    assert len(calls) == 1
    for n in range(70):
        d.catalog(str(n), cfg={'type': 'openai'})
    assert len(d._CACHE) == 64


def test_busy_discovery_keeps_default(monkeypatch):
    install()
    for _ in range(4):
        d._SLOTS.acquire()
    try:
        result = d.catalog('local')
        assert result['status'] == 'refreshing' and result['models'] == ['default']
    finally:
        for _ in range(4):
            d._SLOTS.release()


def test_openai_catalog_auth_prefix_and_unknown_capabilities(monkeypatch):
    cfg = install(endpoint='https://provider.test/prefix/v1', api_key='secret')
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'model-b'}, {'id': 'model-a'}, {'id': 'model-a'}]})
    transport(monkeypatch, serve)
    result = d.catalog('local')
    assert result['models'] == ['default', 'model-a', 'model-b']
    assert str(requests[0].url) == cfg['endpoint'] + '/models'
    assert requests[0].headers['Authorization'] == 'Bearer secret'
    assert all(row['supports_tools'] is None for row in result['items'])


def test_ollama_catalog_reports_size_without_loading_models(monkeypatch):
    install(endpoint='http://localhost:11434/prefix/v1')
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={'models': [{'name': 'new:7b', 'size': 123456,
            'details': {'parameter_size': '7B', 'quantization_level': 'Q4_K_M'}}]})
    transport(monkeypatch, serve)
    result = d.catalog('local')
    row = next(row for row in result['items'] if row['id'] == 'new:7b')
    assert row['parameter_size'] == '7B' and row['quantization'] == 'Q4_K_M'
    assert row['loaded'] is None and row['supports_tools'] is None
    assert str(requests[0].url) == 'http://localhost:11434/prefix/api/tags'
    assert [r.method for r in requests] == ['GET']


def test_lmstudio_separates_embeddings_and_reports_unloaded_models(monkeypatch):
    install(endpoint='http://localhost:1234/v1')
    transport(monkeypatch, lambda r: httpx.Response(200, json={'models': [
        {'key': 'new-llm', 'display_name': 'New LLM', 'type': 'llm', 'loaded_instances': [],
         'max_context_length': 32000, 'quantization': {'name': 'Q8'}, 'params_string': '8B',
         'capabilities': {'vision': True, 'trained_for_tool_use': False}},
        {'key': 'embedding', 'type': 'embedding'},
    ]}))
    result = d.catalog('local')
    assert result['models'] == ['default', 'new-llm']
    row = next(row for row in result['items'] if row['id'] == 'new-llm')
    assert row['name'] == 'New LLM' and row['loaded'] is False
    assert row['supports_tools'] is False and row['supports_vision'] is True
    assert row['context_window'] == 32000


@pytest.mark.parametrize('status', [404, 405, 501])
def test_older_native_api_falls_back_to_openai(monkeypatch, status):
    install(endpoint='http://localhost:1234/v1')
    paths = []
    def serve(request):
        paths.append(request.url.path)
        if request.url.path == '/api/v1/models':
            return httpx.Response(status)
        return httpx.Response(200, json={'data': [{'id': 'legacy'}]})
    transport(monkeypatch, serve)
    result = d.catalog('local')
    assert result['source'] == 'openai' and 'legacy' in result['models']
    assert paths == ['/api/v1/models', '/v1/models']


@pytest.mark.parametrize('status', [301, 401, 403, 429, 500])
def test_status_errors_are_safe_and_redirects_never_forward_credentials(monkeypatch, status):
    install(api_key='PRIVATE-KEY')
    requests = []
    def serve(request):
        requests.append(request)
        return httpx.Response(status, text='PRIVATE-KEY private response', headers={'Location': 'https://other.test/models'})
    transport(monkeypatch, serve)
    result = d.catalog('local')
    assert result['status'] == 'error' and result['models'] == ['default']
    assert len(requests) == 1 and 'PRIVATE' not in json.dumps(result)
    assert str(status) in result['error'] or status in {301, 429}


@pytest.mark.parametrize('body', [b'not JSON', b'[]', b'{"data": {}}', b'{"data": [12]}',
                                b'{"data": [{}]}', b'x' * (d.MAX_BYTES + 1),
                                json.dumps({'data': [{'id': 'x' * 513}]}).encode()])
def test_malformed_and_oversized_response_retains_configured_model(monkeypatch, body):
    install()
    transport(monkeypatch, lambda r: httpx.Response(200, content=body))
    assert d.catalog('local')['status'] == 'error'


def test_preview_preserves_secret_without_saving_and_roundtrips_modes(client, monkeypatch):
    original = install(api_key='PRIVATE-KEY', models=['old'])
    seen = []
    monkeypatch.setattr(d, 'discover', lambda cfg: seen.append(cfg) or ([d.item('new')], False, 'openai'))
    data = {'name': 'local', 'endpoint': 'https://new.test/v1', 'model_discovery': 'automatic',
            'discovery_api': 'lmstudio', 'api_key': ''}
    preview = client.post('/api/admin/config/profiles/discover', json=data)
    assert preview.status_code == 200 and preview.json()['models'] == ['new']
    assert seen[0]['api_key'] == 'PRIVATE-KEY'
    assert 'PRIVATE' not in preview.text
    assert config.get_profile('local') == original
    saved = client.put('/api/admin/config/profiles', json={'profiles': [{**data, 'model': 'new'}]})
    assert saved.status_code == 200 and 'PRIVATE' not in saved.text
    assert config.get_profile('local')['model_discovery'] == 'automatic'
    assert config.get_profile('local')['discovery_api'] == 'lmstudio'
    assert config.get_profile('local')['api_key'] == 'PRIVATE-KEY'


def test_preview_requires_admin_and_unknown_profiles_return_404(client):
    from app.main import app
    from app.auth.deps import require_admin
    from fastapi import HTTPException
    def refuse():
        raise HTTPException(403, 'Admin only')
    app.dependency_overrides[require_admin] = refuse
    try:
        assert client.post('/api/admin/config/profiles/discover', json={'name': 'local'}).status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin)
    assert client.get('/api/providers/missing/models').status_code == 404


def test_bedrock_lists_on_demand_and_inference_profiles_without_invocation(monkeypatch):
    install(type='bedrock', aws_region='us-west-2')
    class Bedrock:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def list_foundation_models(self, **kwargs):
            assert kwargs == {'byOutputModality': 'TEXT', 'byInferenceType': 'ON_DEMAND'}
            return {'modelSummaries': [{'modelId': 'foundation', 'modelName': 'Foundation', 'inputModalities': ['TEXT', 'IMAGE']}]}
        def list_inference_profiles(self, **kwargs):
            return {'inferenceProfileSummaries': [{'inferenceProfileId': 'us.model', 'status': 'ACTIVE'}]}
    monkeypatch.setattr(d, '_bedrock_client', lambda cfg: Bedrock())
    result = d.catalog('local')
    assert result['models'] == ['default', 'foundation', 'us.model']
    assert result['source'] == 'bedrock'


def test_bedrock_bearer_key_explains_curated_fallback():
    install(type='bedrock', aws_bedrock_api_key='PRIVATE-KEY')
    result = d.catalog('local')
    assert result['status'] == 'error' and 'curated' in result['error']
    assert 'PRIVATE' not in json.dumps(result)
