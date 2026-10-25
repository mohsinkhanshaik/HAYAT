"""Team size comes from this machine's brain window, measured times and CPUs, not a fixed number."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agents.capacity import DEFAULT_WORKER_SECONDS, MIN_WORKER_CHARS, measure, measured_worker_seconds
from app.agents.team import TeamsBusy, WorkerTeam, capacity_question


def machine(cpus=32):
    gpu = SimpleNamespace(total_bytes=8 * 1024**3)
    return SimpleNamespace(gpu=gpu, available_ram_bytes=16 * 1024**3, cpu_count=cpus,
                           summary=lambda: "Running on a test machine.")


def test_a_bigger_brain_window_allows_a_bigger_team():
    small = measure(SimpleNamespace(num_ctx=8192), hardware_profile=machine())
    large = measure(SimpleNamespace(num_ctx=32768), hardware_profile=machine())
    assert 3 <= small.max_workers < large.max_workers
    # Every worker of a full team still gets a useful share, and nothing is cut off the end.
    for capacity in (small, large):
        assert capacity.share_chars(capacity.max_workers) >= MIN_WORKER_CHARS
        assert capacity.share_chars(capacity.max_workers) * capacity.max_workers <= capacity.material_chars() + MIN_WORKER_CHARS
    assert "16,384" not in small.explain() and "8,192 tokens" in small.explain()


def test_budgets_grow_with_the_team_and_concurrency_follows_cpus_and_brain(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "4")
    capacity = measure(SimpleNamespace(num_ctx=16384), hardware_profile=machine(cpus=32))
    assert capacity.team_tool_calls(20) > capacity.team_tool_calls(6) >= 40
    assert capacity.team_seconds(20) > capacity.team_seconds(6) >= 1200
    assert capacity.concurrent_teams == 4
    assert measure(SimpleNamespace(num_ctx=16384), hardware_profile=machine(cpus=4)).concurrent_teams == 1
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "1")
    assert measure(SimpleNamespace(num_ctx=16384), hardware_profile=machine()).concurrent_teams == 1


def test_worker_time_is_learned_from_this_machines_runs(tmp_path):
    assert measured_worker_seconds(tmp_path) == (DEFAULT_WORKER_SECONDS, 0)
    for index, (workers, seconds) in enumerate(((4, 240), (5, 400), (2, 100))):
        (tmp_path / f"team-{index}.json").write_text(json.dumps(
            {"workers": [{}] * workers, "seconds": seconds, "finished": True}), encoding="utf-8")
    (tmp_path / "team-broken.json").write_text("{", encoding="utf-8")
    assert measured_worker_seconds(tmp_path) == (60.0, 3)


def test_running_teams_are_limited_with_a_plain_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "1")
    team = WorkerTeam(None, SimpleNamespace(names=lambda: []), root=tmp_path)
    team._running["team-busy"] = SimpleNamespace()
    with pytest.raises(TeamsBusy, match="runs at most 1 at once"):
        team.start("research 20 companies and their jobs")


@pytest.mark.parametrize("text", ["how many agents can you run", "what is your agent limit",
                                  "how many workers can you create on this machine"])
def test_capacity_questions_are_answered_from_measurement(text):
    assert capacity_question(text)


@pytest.mark.parametrize("text", ["research 20 companies and find jobs for me", "how many companies are hiring"])
def test_ordinary_requests_are_not_capacity_questions(text):
    assert not capacity_question(text)


def test_hayat_answers_how_many_agents_from_its_machine(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    rt.teams = WorkerTeam(SimpleNamespace(num_ctx=16384), SimpleNamespace(names=lambda: []), root=tmp_path)
    answer = rt.assistant.handle("how many agents can you run?").display_response
    assert answer.startswith("I can run teams of up to")
    assert "16,384 tokens" in answer and "one after another" in answer
    assert rt.manager.calls == []
