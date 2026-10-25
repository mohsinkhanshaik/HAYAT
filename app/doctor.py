from __future__ import annotations

from typing import Any


def inspect_runtime(runtime) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    def run(name: str, callback) -> None:
        try:
            detail = callback()
            signals = detail if isinstance(detail, dict) else {}
            degraded = any(signals.get(key) is False for key in ("available", "configured", "registered", "healthy", "installed"))
            failed = str(signals.get("state", "")).upper() in {"ERROR", "FAILED"}
            checks[name] = {"status": "error" if failed else "degraded" if degraded else "ready", "detail": detail}
        except Exception as exc:
            checks[name] = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}

    run("conversation_routing", lambda: _conversation_check(runtime))
    run("planner", lambda: {"model": getattr(runtime.agent.planner.model, "name", None), "max_steps": runtime.agent.planner.max_steps})
    run("model", lambda: _model_check(runtime))
    run("memory", lambda: runtime.memory.stats())
    run("knowledge", lambda: runtime.research.knowledge.stats())
    run("voice", lambda: _voice_check(runtime))
    run("microphone_speaker", lambda: _voice_hardware_check(runtime))
    run("vision", lambda: {"configured": runtime.vision is not None, "provider": getattr(getattr(runtime.vision, "provider", None), "name", None), "model": getattr(getattr(runtime.vision, "provider", None), "model", None)})
    run("browser", lambda: {"registered": all(name in runtime.registry.names() for name in ("browser_launch", "browser_navigate", "browser_read_page")), "isolated": True})
    run("windows_automation", lambda: {"registered": all(name in runtime.registry.names() for name in ("application_state", "notepad_edit", "close_application")), "calculator_windows": runtime.registry.get("application_state").handler("calculator")["window_count"]})
    run("hardware", lambda: _hardware_check())
    run("storage", lambda: runtime.storage.status())
    run("permissions", lambda: {"policies": len(runtime.registry.permissions.policies), "protected_paths": len(runtime.registry.permissions.config.get("protected_paths", []))})
    run("research", lambda: {"database": str(runtime.research.knowledge.path), "search_provider": getattr(runtime.research.search, "name", type(runtime.research.search).__name__)})
    run("cost_policy", lambda: _cost_check(runtime))
    run("camera", lambda: runtime.camera.status())
    run("displays", lambda: runtime.displays.status())
    run("clipboard", lambda: runtime.clipboard.status())
    run("observability", lambda: runtime.observability.health(runtime))
    run("downloads", lambda: runtime.downloads.status())
    run("backups", lambda: runtime.backups.status())
    for name, attribute in (("training", "training"), ("learning", "learning"), ("global_intelligence", "intelligence")):
        if getattr(runtime, attribute, None) is not None:
            run(name, lambda attribute=attribute: _component_check(runtime, attribute))
    if "image_generation_status" in runtime.registry.names():
        run("image_generation", lambda: runtime.registry.get("image_generation_status").handler())
    if getattr(runtime, "scheduler", None) is not None:
        run("automation", lambda: _automation_check(runtime))
    counts = {status: sum(item["status"] == status for item in checks.values()) for status in ("ready", "degraded", "error")}
    return {"ready": not counts["degraded"] and not counts["error"], "checks": checks, "summary": counts,
            "attention": [name for name, item in checks.items() if item["status"] != "ready"],
            "scope": "Configuration and component checks; live media, speech quality and model reasoning require their integration benchmarks."}


def _model_check(runtime) -> dict[str, Any]:
    providers = runtime.router.available()
    available = any(providers.values()) if isinstance(providers, dict) else bool(providers)
    detail: dict[str, Any] = {"available": available, "providers": providers}
    if not available:
        # Empty provider lists alone read as "broken"; usually HAYAT's own model server is simply not started.
        import os

        from app.portable import PORT

        host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or ""
        detail["why"] = (f"No model server answered. HAYAT's own Ollama (port {PORT}) starts with HAYAT; run this check "
                         "while HAYAT is open, or start it with start.ps1." if str(PORT) in host else
                         "No model server answered at the configured address; start Ollama or LM Studio, or open HAYAT.")
    return detail


def _voice_check(runtime) -> dict[str, Any]:
    if runtime.voice is None:
        return {"available": False}
    detail = runtime.voice.status()
    tts = getattr(runtime.voice, "tts", None)
    synthesis = tts.status() if tts is not None and callable(getattr(tts, "status", None)) else {}
    return {**detail, "available": synthesis.get("installed", True), "synthesis": synthesis}


def _component_check(runtime, attribute: str) -> dict[str, Any]:
    component = getattr(runtime, attribute)
    detail = component.status()
    if attribute == "training":
        return {**detail, "healthy": bool(detail.get("validation", {}).get("passed"))}
    return detail


def _automation_check(runtime) -> dict[str, Any]:
    scheduler = runtime.scheduler
    jobs = scheduler.repository.list()
    return {"configured": True, "running": bool(scheduler.scheduler.running), "jobs": len(jobs),
            "enabled_jobs": sum(bool(job.enabled) for job in jobs),
            "overlap_policy": "one execution per job", "missed_runs": "coalesced"}


def _voice_hardware_check(runtime) -> dict[str, Any]:
    if runtime.voice is None:
        raise RuntimeError("Voice runtime is unavailable.")
    inventory = runtime.voice.devices.list_devices()
    diagnostics = runtime.voice.devices.diagnose_input()
    if not diagnostics["available"]:
        errors = diagnostics.get("windows_device_errors") or []
        detail = errors[0] if errors else diagnostics["reason"]
        raise RuntimeError(f"No recording endpoint is available: {detail}")
    return inventory | {"input_diagnostics": diagnostics}


def _hardware_check() -> dict[str, Any]:
    """What this machine is, and which locally-held models it can actually run.

    Reported together on purpose: the numbers only matter as the answer to
    "can this run?", and models are read from disk rather than from a running
    service, so this stays truthful when nothing is serving.
    """
    from app.hardware import profile
    from app.models.gguf import discover_local_models

    hardware = profile()
    models = discover_local_models()
    return hardware.to_dict() | {
        "local_models": [
            model.to_dict() | {"verdict": hardware.can_run(model.size_bytes)}
            for model in models[:10]
        ],
        "local_models_found": len(models),
    }


def _conversation_check(runtime) -> dict[str, Any]:
    intent = runtime.intent_router.route("Hi", runtime.session)
    if intent.requires_tools or intent.intent.value != "CONVERSATION":
        raise AssertionError("Greeting would reach tools or planner.")
    return {"intent": intent.intent.value, "requires_tools": intent.requires_tools}


def _cost_check(runtime) -> dict[str, Any]:
    runtime.cost_policy.assert_zero_cost()
    available = runtime.router.available()
    forbidden = sorted(set(available) & {"openai", "anthropic", "gemini"})
    if forbidden:
        raise AssertionError(f"Paid/cloud providers are reachable in ZERO-COST mode: {forbidden}")
    return runtime.cost_policy.status() | {"reachable_runtime_providers": sorted(available)}
