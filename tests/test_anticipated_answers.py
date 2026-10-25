from types import SimpleNamespace

from app.assistant import AssistantService


def _service(prediction, recalled=None, fresh="fresh answer", modes=()):
    calls = []

    class Research:
        def recall_verified(self, text, **kwargs):
            calls.append(("recall", text, kwargs))
            return recalled

        def research(self, text, **kwargs):
            calls.append(("research", text, kwargs))
            return SimpleNamespace(answer=fresh)

    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(
        learner=SimpleNamespace(anticipated_match=lambda text: prediction),
        research=Research(),
        session=SimpleNamespace(effective_modes=list(modes)),
        authority=SimpleNamespace(privacy_mode=False),
        state=SimpleNamespace(set_activity=lambda value: None),
    )
    return service, calls


def test_matching_prediction_uses_verified_local_answer_without_search():
    service, calls = _service(
        {"matched": True}, recalled=SimpleNamespace(answer="prepared verified answer")
    )
    answer = service._anticipated_question_answer("same question")
    assert answer == "prepared verified answer"
    assert [call[0] for call in calls] == ["recall"]


def test_related_but_different_question_gets_fresh_research():
    service, calls = _service({"matched": False, "related_but_different": True})
    assert service._anticipated_question_answer("different facet") == "fresh answer"
    assert [call[0] for call in calls] == ["research"]
    assert calls[0][2]["remember"] is True


def test_private_mode_never_turns_a_prediction_miss_into_network_work():
    service, calls = _service(
        {"matched": False, "related_but_different": True}, modes=("PRIVATE",)
    )
    assert service._anticipated_question_answer("different facet") is None
    assert calls == []
