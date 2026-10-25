from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.base import ModelError, ModelResponse
from app.models.intelligent_router import BoundedFallbackProvider, IntelligentModelRouter, RouteRequest
from app.supervisor import CircuitBreaker


def router(tmp_path):
    rows = [{"model": name, "provider": "ollama", "capabilities": ["completion"],
             "size_bytes": 1, "benchmark_score": {"coding_accuracy": score,
                                                  "mean_latency_seconds": 1}}
            for name, score in (("best", .95), ("backup", .7))]
    return IntelligentModelRouter(SimpleNamespace(model_inventory=lambda: {"models": rows}),
        SimpleNamespace(providers={"ollama": SimpleNamespace(model="best")}), tmp_path / "routing.db")


def test_repeated_provider_failure_yields_to_backup_then_recovers(tmp_path):
    brain = router(tmp_path)
    clock = [100.0]
    brain._health["best"] = CircuitBreaker(clock=lambda: clock[0])
    request = RouteRequest("coding")
    brain._record_failure("best", 20, "connection refused")
    assert brain.route(request).primary == "best", "one failure must not blacklist a model"
    for _ in range(2):
        brain._record_failure("best", 20, "connection refused")
    assert brain.route(request).primary == "backup"
    assert brain.status()["model_health"]["best"]["retry_after_seconds"] == 60
    clock[0] += 61
    assert brain.route(request).primary == "best"
    brain._record_success("best")
    assert brain.status()["model_health"]["best"]["consecutive_failures"] == 0


def test_no_model_is_created_when_every_resource_check_refuses(tmp_path):
    brain = router(tmp_path)
    brain.resource_manager = SimpleNamespace(can_load=lambda model: {"allowed": False})
    assert brain.provider(brain.route(RouteRequest("coding"))) is None


def test_conversation_cannot_bypass_selector_refusal(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    base = Mock()
    chat = ConversationService(base, memory, "test", model_selector=lambda *_: None)
    assert "eligible" in chat.reply("Explain how a bicycle works")
    base.generate.assert_not_called()


def test_memory_pressure_is_rechecked_before_an_existing_route_runs(tmp_path):
    brain = router(tmp_path)
    admission = {"allowed": True}
    brain.resource_manager = SimpleNamespace(can_load=lambda model: admission)
    provider = brain.provider(brain.route(RouteRequest("coding")))
    admission["allowed"] = False
    with pytest.raises(ModelError, match="insufficient resources"):
        provider.generate([])


def test_metric_failure_cannot_stop_fallback_or_retry_a_success():
    failed = SimpleNamespace(model="broken", generate=Mock(side_effect=ModelError("offline")))
    good = SimpleNamespace(model="good", generate=Mock(return_value=ModelResponse("done", "good", "local")))
    telemetry = Mock(side_effect=OSError("metrics database unavailable"))
    provider = BoundedFallbackProvider([failed, good], telemetry, telemetry)
    assert provider.generate([], max_tokens=42).content == "done"
    good.generate.assert_called_once()
    assert good.generate.call_args.kwargs["max_tokens"] == 42


def test_bounded_fallback_never_tries_a_fourth_provider():
    providers = [SimpleNamespace(model=str(n), generate=Mock(side_effect=ModelError("offline")))
                 for n in range(4)]
    with pytest.raises(ModelError):
        BoundedFallbackProvider(providers).generate([])
    providers[-1].generate.assert_not_called()


def test_empty_output_fails_over_but_valid_tool_call_is_usable():
    empty = SimpleNamespace(model='empty', generate=Mock(return_value=ModelResponse(' ', 'empty', 'local')))
    valid = SimpleNamespace(model='tool', generate=Mock(return_value=ModelResponse('', 'tool', 'local', tool_calls=[{'name': 'search'}])))
    failure, success = Mock(), Mock()
    result = BoundedFallbackProvider([empty, valid], failure, success).generate([])
    assert result.tool_calls
    failure.assert_called_once()
    success.assert_called_once_with('tool')


def test_nonstreaming_cancellation_never_tries_backup_or_records_failure():
    from app.models.streaming import GenerationCancelled
    primary = SimpleNamespace(model='primary', generate=Mock(side_effect=GenerationCancelled('cancelled')))
    backup = SimpleNamespace(model='backup', generate=Mock())
    failure = Mock()
    with pytest.raises(GenerationCancelled):
        BoundedFallbackProvider([primary, backup], failure).generate([])
    backup.generate.assert_not_called()
    failure.assert_not_called()


@pytest.mark.parametrize('latency', [float('nan'), float('inf'), -3000, 'invalid'])
def test_invalid_latency_cannot_poison_scores_or_quality_learning(tmp_path, latency):
    brain = router(tmp_path)
    rows = brain._inventory()
    rows[0]['benchmark_score']['mean_latency_seconds'] = latency
    rows[0]['size_bytes'] = latency
    decision = brain.route(RouteRequest('coding'))
    import math
    assert all(math.isfinite(item['score']) and item['latency_ms'] >= 0 for item in decision.scores)
    brain.record_outcome('coding', 'best', True, latency, {'passed': True})
    assert brain._outcomes('coding', 'best')['count'] == 0


def test_unverified_success_and_truthy_strings_do_not_train_router(tmp_path):
    brain = router(tmp_path)
    brain.record_outcome('coding', 'best', True, 50, {})
    brain.record_outcome('coding', 'best', True, 50, {'passed': False})
    brain.record_outcome('coding', 'best', 'yes', 50, {'passed': True})
    assert brain._outcomes('coding', 'best')['count'] == 0
