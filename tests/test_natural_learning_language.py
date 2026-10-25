
from app.runtime import build_runtime


def test_plain_learn_routes_to_learning():
    runtime = build_runtime(
        use_model=False,
        start_background_services=False,
    )

    try:
        for request in (
            "learn arabic",
            "learn coding",
            "learn coding and self upgrade",
            "learn about docker networking",
        ):
            intent = runtime.intent_router.route(
                request,
                runtime.session,
            )

            assert intent.intent.value == "LEARNING", (
                request,
                intent.intent.value,
            )

            assert intent.requires_tools is False
            assert intent.requires_research is False
    finally:
        runtime.close()


def test_explicit_research_still_routes_to_research():
    runtime = build_runtime(
        use_model=False,
        start_background_services=False,
    )

    try:
        intent = runtime.intent_router.route(
            "research Arabic language",
            runtime.session,
        )

        assert intent.intent.value == "RESEARCH"
        assert intent.requires_research is True
    finally:
        runtime.close()
