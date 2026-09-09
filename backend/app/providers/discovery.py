"""Read-only model catalogs. No inference, downloads, loads, or config writes.

Catalogs are shared only within a configured profile/credential identity. Bounded
process-local caching coalesces refreshes and retains the last successful result.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import get_profile

logger = logging.getLogger('phlox.discovery')
MAX_MODELS = 2000
MAX_BYTES = 2 * 1024 * 1024
CACHE_SECONDS = 60
MIN_REFRESH_SECONDS = 3
_CACHE = OrderedDict()
_CONDITION = threading.Condition()
_PENDING = set()
_SLOTS = threading.BoundedSemaphore(4)


class DiscoveryError(ValueError):
    """Only locally authored, credential-free messages may cross the API boundary."""


def mode(cfg):
    return cfg.get('model_discovery') or ('manual' if cfg.get('models') else 'automatic')


def api_kind(cfg):
    if cfg.get('type') == 'bedrock':
        return 'bedrock'
    selected = cfg.get('discovery_api') or 'auto'
    if selected != 'auto':
        return selected
    try:
        port = urlsplit(cfg.get('endpoint') or '').port
    except ValueError:
        port = None
    return {11434: 'ollama', 1234: 'lmstudio'}.get(port, 'openai')


def _text(value, limit=512):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    return value.strip()[:limit] or None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 10**16 else None


def _bool(value):
    return value if isinstance(value, bool) else None


def item(model_id, **values):
    if not isinstance(model_id, str) or len(model_id.strip()) > 512:
        return None
    model_id = _text(model_id)
    if not model_id:
        return None
    if 'name' in values:
        values['name'] = _text(values['name']) or model_id
    return {'id': model_id, 'name': model_id, 'kind': 'unknown', 'supports_tools': None,
            'supports_vision': None, 'context_window': None, 'loaded': None,
            'size_bytes': None, 'parameter_size': None, 'quantization': None,
            'discovered': True, **values}


def configured(cfg):
    ids = [cfg.get('model'), *(cfg.get('models') or [])]
    return [row for value in ids if (row := item(value, discovered=False))]


def _merge(items, cfg):
    result = {row['id']: row for row in configured(cfg)}
    result.update({row['id']: row for row in items if row})
    return sorted(result.values(), key=lambda row: row['id'].casefold())


def _payload(items, cfg, **extra):
    return {'models': [row['id'] for row in items if row['kind'] != 'embedding'],
            'items': items, 'mode': mode(cfg), 'source': api_kind(cfg),
            'status': 'ready', 'stale': False, 'error': None, 'checked_at': None,
            'last_success_at': None, 'limited': False, **extra}


def _endpoint(cfg, suffix, native=False):
    raw = cfg.get('endpoint') or 'https://api.openai.com/v1'
    try:
        parts = urlsplit(raw)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.port == 0):
            raise ValueError()
        path = parts.path.rstrip('/')
        if native and path.endswith('/v1'):
            path = path[:-3]
        return urlunsplit((parts.scheme, parts.netloc, path + suffix, '', ''))
    except ValueError:
        raise DiscoveryError('Use an HTTP(S) provider endpoint without URL credentials, query parameters, or fragments.') from None


def _json(cfg, url):
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if cfg.get('api_key'):
        headers['Authorization'] = 'Bearer ' + cfg['api_key']
    # Providers may be local/private by design. Only admins configure their address.
    # Do not forward credentials to a redirect target or use environment proxies.
    until = time.monotonic() + 12
    with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', url, headers=headers) as response:
            response.raise_for_status()
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise DiscoveryError('The model endpoint ignored the uncompressed-response request. Use a curated list or adjust the provider proxy.')
            data = bytearray()
            for block in response.iter_bytes(chunk_size=16384):
                if time.monotonic() >= until:
                    raise DiscoveryError('Model discovery timed out. Try Refresh when the provider is ready.')
                data.extend(block)
                if len(data) > MAX_BYTES:
                    raise DiscoveryError('The provider model catalog exceeded the 2 MiB limit.')
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                raise DiscoveryError('The provider returned an invalid model catalog.')
            return parsed


def _rows(data, key):
    rows = data.get(key)
    if not isinstance(rows, list):
        raise DiscoveryError('The provider returned an invalid model catalog.')
    if any(not isinstance(row, dict) for row in rows[:MAX_MODELS]):
        raise DiscoveryError('The provider returned an invalid model catalog.')
    return rows[:MAX_MODELS], len(rows) > MAX_MODELS


def _openai(cfg):
    rows, limited = _rows(_json(cfg, _endpoint(cfg, '/models')), 'data')
    # Generic model IDs do not reliably establish modality or tool support.
    return [item(row.get('id')) for row in rows], limited, 'openai'


def _ollama(cfg):
    rows, limited = _rows(_json(cfg, _endpoint(cfg, '/api/tags', native=True)), 'models')
    result = []
    for row in rows:
        details = row.get('details') if isinstance(row.get('details'), dict) else {}
        result.append(item(row.get('name') or row.get('model'),
                           size_bytes=_number(row.get('size')),
                           parameter_size=_text(details.get('parameter_size'), 40),
                           quantization=_text(details.get('quantization_level'), 40)))
    return result, limited, 'ollama'


def _lmstudio(cfg):
    rows, limited = _rows(_json(cfg, _endpoint(cfg, '/api/v1/models', native=True)), 'models')
    result = []
    for row in rows:
        caps = row.get('capabilities') if isinstance(row.get('capabilities'), dict) else {}
        quant = row.get('quantization') if isinstance(row.get('quantization'), dict) else {}
        result.append(item(row.get('key'), name=_text(row.get('display_name')) or row.get('key'),
                           kind=row.get('type') if row.get('type') in {'llm', 'embedding'} else 'unknown',
                           supports_tools=_bool(caps.get('trained_for_tool_use')),
                           supports_vision=_bool(caps.get('vision')),
                           context_window=_number(row.get('max_context_length')),
                           loaded=bool(row['loaded_instances']) if isinstance(row.get('loaded_instances'), list) else None,
                           size_bytes=_number(row.get('size_bytes')),
                           parameter_size=_text(row.get('params_string'), 40),
                           quantization=_text(quant.get('name'), 40)))
    return result, limited, 'lmstudio'


def _bedrock_client(cfg):
    import boto3
    from botocore.config import Config

    if cfg.get('aws_bedrock_api_key'):
        raise DiscoveryError('Bedrock catalog discovery requires AWS credentials with listing permissions. Use a curated list for a Bedrock bearer-key profile.')
    kwargs = {target: cfg[source] for source, target in (
        ('aws_region', 'region_name'), ('aws_profile', 'profile_name'),
        ('aws_access_key_id', 'aws_access_key_id'), ('aws_secret_access_key', 'aws_secret_access_key'),
        ('aws_session_token', 'aws_session_token')) if cfg.get(source)}
    return boto3.Session(**kwargs).client('bedrock', config=Config(
        connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1}))


def _bedrock(cfg):
    # Listing is not proof of invocation access or Converse compatibility.
    with _bedrock_client(cfg) as client:
        rows = client.list_foundation_models(byOutputModality='TEXT', byInferenceType='ON_DEMAND').get('modelSummaries', [])
        result = [item(row.get('modelId'), name=_text(row.get('modelName')) or row.get('modelId'),
                       kind='llm', supports_vision='IMAGE' in row.get('inputModalities', []))
                  for row in rows[:MAX_MODELS] if row.get('modelLifecycle', {}).get('status') != 'LEGACY']
        limited = len(rows) > MAX_MODELS
        token = None
        for _ in range(4):
            response = client.list_inference_profiles(**({'nextToken': token} if token else {}))
            for row in response.get('inferenceProfileSummaries', []):
                if row.get('status') == 'ACTIVE':
                    result.append(item(row.get('inferenceProfileId'), name=_text(row.get('inferenceProfileName')) or row.get('inferenceProfileId')))
            token = response.get('nextToken')
            if not token:
                break
        return result[:MAX_MODELS], limited or bool(token) or len(result) > MAX_MODELS, 'bedrock'


def discover(cfg):
    kind = api_kind(cfg)
    try:
        return {'openai': _openai, 'ollama': _ollama, 'lmstudio': _lmstudio, 'bedrock': _bedrock}[kind](cfg)
    except httpx.HTTPStatusError as exc:
        # Older local servers can still expose their OpenAI-compatible model IDs.
        if kind in {'ollama', 'lmstudio'} and exc.response.status_code in {404, 405, 501}:
            return _openai(cfg)
        raise


def _error(exc):
    if isinstance(exc, DiscoveryError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return f'Model discovery was denied (HTTP {status}). Check the provider credentials and listing permissions.'
        if status == 429:
            return 'The provider rate-limited model discovery. Wait briefly, then Refresh.'
        if 300 <= status < 400:
            return 'The model endpoint redirected the request. Configure its final URL; credentials are not forwarded.'
        return f'The model endpoint returned HTTP {status}. Check the provider endpoint and discovery API.'
    if isinstance(exc, httpx.TimeoutException):
        return 'Model discovery timed out. Check the provider, then Refresh.'
    if isinstance(exc, httpx.ConnectError):
        return 'Could not connect to the model server. Make sure it is running and reachable from Phlox.'
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return 'The provider returned an unsupported model catalog. Check the discovery API or use a curated list.'
    return 'Model discovery failed. Check the provider credentials, region, and model-listing permissions.'


def _key(name, cfg):
    return hashlib.sha256(json.dumps([name, cfg], sort_keys=True, default=str).encode()).hexdigest()


def catalog(name, *, refresh=False, cfg=None):
    cfg = deepcopy(cfg if cfg is not None else get_profile(name))
    if cfg is None:
        raise KeyError(name)
    if mode(cfg) == 'manual':
        return _payload(_merge([], cfg), cfg, source='manual')
    key = _key(name, cfg)
    with _CONDITION:
        if key in _PENDING:
            _CONDITION.wait_for(lambda: key not in _PENDING, timeout=8)
        cached = _CACHE.get(key)
        age = time.monotonic() - cached['attempt'] if cached else float('inf')
        if cached and (age < MIN_REFRESH_SECONDS or (not refresh and age < CACHE_SECONDS)):
            _CACHE.move_to_end(key)
            return deepcopy(cached['payload'])
        if key in _PENDING or not _SLOTS.acquire(blocking=False):
            result = deepcopy(cached['payload']) if cached else _payload(_merge([], cfg), cfg)
            return {**result, 'status': 'refreshing', 'stale': True,
                    'error': 'Model discovery is busy. Refresh again shortly.'}
        _PENDING.add(key)
    try:
        items, limited, source = discover(cfg)
        if any(row is None for row in items):
            raise DiscoveryError('The provider returned a model without a valid ID. Check the discovery API or use a curated list.')
        now = datetime.now(timezone.utc).isoformat()
        result = _payload(_merge(items, cfg), cfg, source=source, limited=limited,
                          checked_at=now, last_success_at=now)
    except Exception as exc:  # never return raw provider messages, bodies, URLs or credentials
        logger.warning('Model discovery failed error_type=%s', type(exc).__name__)
        result = deepcopy(cached['payload']) if cached else _payload(_merge([], cfg), cfg)
        result.update(status='error', stale=bool(result['last_success_at']), error=_error(exc),
                      checked_at=datetime.now(timezone.utc).isoformat())
    finally:
        _SLOTS.release()
    with _CONDITION:
        _CACHE[key] = {'attempt': time.monotonic(), 'payload': result}
        _CACHE.move_to_end(key)
        while len(_CACHE) > 64:
            _CACHE.popitem(last=False)
        _PENDING.discard(key)
        _CONDITION.notify_all()
    return deepcopy(result)
