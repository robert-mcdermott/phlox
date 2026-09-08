"""Strict document embeddings. Memory's legacy embedder is a separate compatibility path."""
import hashlib
import json
import math

from app.config import get_embeddings_config, get_profile
from app.rag.embed import _hash_embed


class EmbeddingError(RuntimeError):
    pass


def identity():
    cfg = get_embeddings_config()
    profile_name = cfg.get('profile')
    if not profile_name:
        values = {'profile': None, 'model': 'local-hash', 'version': '1', 'provider': 'hash'}
    else:
        profile = get_profile(profile_name)
        if not profile or profile.get('type', 'openai') != 'openai':
            raise EmbeddingError('Embedding profile is missing or cannot embed. Check embeddings configuration.')
        values = {'profile': profile_name, 'model': cfg.get('model', 'text-embedding-3-small'),
                  'version': str(cfg.get('version', '1')), 'provider': 'openai',
                  'endpoint_hash': hashlib.sha256(profile.get('endpoint', 'https://api.openai.com/v1').encode()).hexdigest()}
    values['fingerprint'] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return values


def embed(texts, expected=None):
    spec = identity()
    if expected and spec['fingerprint'] != expected['fingerprint']:
        raise EmbeddingError('Embedding configuration changed during processing. Retry with consistent settings.')
    if spec['provider'] == 'hash':
        vectors = [_hash_embed(text) for text in texts]
    else:
        from app.providers.registry import build_provider
        try:
            vectors = build_provider(spec['profile']).embed(texts, model=spec['model'])
        except Exception:
            raise EmbeddingError('Embedding service unavailable. Check the provider and retry; existing vectors were preserved.') from None
    validate(vectors, len(texts))
    if identity()['fingerprint'] != spec['fingerprint']:
        raise EmbeddingError('Embedding configuration changed during processing. Retry.')
    return vectors, {**spec, 'dimensions': len(vectors[0]) if vectors else 0}


def validate(vectors, count):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingError('Embedding service returned an incomplete batch. No document was published.')
    dimension = len(vectors[0]) if vectors and isinstance(vectors[0], list) else 0
    if count and not 0 < dimension <= 8192:
        raise EmbeddingError('Embedding service returned invalid dimensions.')
    if any(not isinstance(v, list) or len(v) != dimension or
           any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in v) for v in vectors):
        raise EmbeddingError('Embedding service returned inconsistent or invalid vectors.')
