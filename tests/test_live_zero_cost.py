"""Explicit manual smoke check of installed local models; no cloud API path."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.getenv('HAYAT_LIVE_ZERO_COST') != '1', reason='manual local-model verification')


def test_installed_chat_and_embeddings_remain_local():
    from app.config import load_settings, load_yaml
    from app.memory.embeddings import OllamaEmbeddingProvider
    from app.models.router import ModelRouter
    from app.policy.cost import CostPolicy, is_local_endpoint

    policy = CostPolicy.from_settings(load_settings()['cost_policy'])
    policy.assert_zero_cost()
    config = load_yaml('config/models.yaml')
    router = ModelRouter(config, policy)
    provider = router.get()
    assert provider is not None and provider.name == 'ollama'
    assert is_local_endpoint(provider.base_url)
    answer = provider.generate([{'role': 'user', 'content': 'Reply with one short greeting.'}], max_tokens=24)
    assert answer.content.strip()
    assert answer.provider == 'ollama'
    assert not answer.model.lower().endswith('cloud')
    embeddings = OllamaEmbeddingProvider(provider.base_url, 'all-minilm:latest')
    try:
        assert embeddings.available
        result = embeddings.embed(['Local zero-cost verification'])
        assert len(result) == 1 and len(result[0]) > 10
    finally:
        if getattr(embeddings, '_http', None):
            embeddings._http.close()
