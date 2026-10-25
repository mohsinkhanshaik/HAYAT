"""Knowing that HAYAT is working, and what it is working on.

A long turn used to look identical to a hung one, and the only way to tell
them apart was to wait and find out -- which is exactly the judgement a person
cannot make. These tests hold the two properties that fix that: work registers
itself while it runs, and the status line reports that register rather than
animating regardless.
"""
from __future__ import annotations

import io
import threading
import time

import pytest

from app.activity import ACTIVITY, ActivityRegistry
from app.output import OutputCoordinator, Spinner


@pytest.fixture
def registry() -> ActivityRegistry:
    return ActivityRegistry()


class _Narrow(io.StringIO):
    encoding = "cp1252"


class _Wide(io.StringIO):
    encoding = "utf-8"


# ------------------------------------------------------ the live register


def test_nothing_is_running_to_begin_with(registry):
    assert not registry.busy
    assert registry.report() == "Nothing is running."


def test_work_is_visible_while_it_runs(registry):
    with registry.track("tool", "web_search", "rtx 5060 reviews"):
        assert registry.busy
        assert registry.current()[0].label == "web_search"
        assert "rtx 5060" in registry.headline()
    assert not registry.busy


def test_work_that_raises_does_not_stay_on_the_list(registry):
    """A spinner still turning for work that stopped is a claim, not a
    decoration."""
    with pytest.raises(ValueError):
        with registry.track("tool", "explode"):
            raise ValueError("boom")
    assert not registry.busy


def test_the_headline_names_the_innermost_step(registry):
    """Work nests, and the innermost step is the one actually running:
    "researching" is true while a search runs, but "searching" is what is
    happening."""
    with registry.track("task", "research"):
        time.sleep(0.01)
        with registry.track("tool", "web_search", "quantum"):
            assert "web_search" in registry.headline()
        assert "research" in registry.headline()


def test_concurrent_work_is_all_listed(registry):
    started = threading.Barrier(3)
    done = threading.Event()

    def worker(name):
        with registry.track("tool", name):
            started.wait()
            done.wait(timeout=5)

    threads = [threading.Thread(target=worker, args=(f"tool-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    started.wait(timeout=5)
    try:
        assert registry.summary()["count"] == 2
    finally:
        done.set()
        for thread in threads:
            thread.join(timeout=5)
    assert not registry.busy


def test_finishing_twice_is_harmless(registry):
    activity = registry.start("tool", "x")
    registry.finish(activity)
    registry.finish(activity)
    registry.finish(None)
    assert not registry.busy


def test_the_report_lists_what_is_running_with_its_age(registry):
    with registry.track("model", "thinking", "llama.cpp/qwen"):
        report = registry.report()
    assert "1 thing(s) running" in report
    assert "thinking" in report and "model" in report


def test_elapsed_time_is_measured(registry):
    with registry.track("tool", "slow") as activity:
        time.sleep(0.05)
        assert activity.elapsed >= 0.05


def test_activity_exposes_a_countdown_and_learns_observed_duration(registry):
    first = registry.start("research", "web research")
    assert first.estimated_seconds == registry.DEFAULT_ESTIMATES["research"]
    assert 0 < first.remaining <= first.estimated_seconds
    time.sleep(0.02)
    registry.finish(first)
    second = registry.start("research", "web research")
    try:
        assert second.estimated_seconds < registry.DEFAULT_ESTIMATES["research"]
        payload = second.to_dict()
        assert payload["remaining_seconds"] <= payload["estimated_seconds"]
    finally:
        registry.finish(second)


def test_a_broken_watcher_does_not_break_the_work(registry):
    registry.add_listener(lambda _: 1 / 0)
    with registry.track("tool", "fine"):
        pass  # must not raise


# ---------------------------------------------------------- the status line


def test_the_spinner_reports_real_work_not_a_generic_message():
    registry = ActivityRegistry()
    coordinator = OutputCoordinator(stream=_Wide())
    spinner = Spinner(coordinator, registry)

    with registry.track("tool", "generate_image", "a blue robot"):
        spinner._draw(spinner._frames[0])

    assert "generate_image" in coordinator._stream.getvalue()


def test_a_frame_is_written_once_not_twice():
    """The first version wrote the line, padding, then the line again --
    doubling every frame's output for no benefit."""
    stream = _Wide()
    spinner = Spinner(OutputCoordinator(stream=stream), ActivityRegistry())
    spinner._draw("|")
    assert stream.getvalue().count("thinking") == 1


def test_a_shorter_label_does_not_leave_the_tail_of_a_longer_one():
    registry = ActivityRegistry()
    stream = _Wide()
    spinner = Spinner(OutputCoordinator(stream=stream), registry)

    long_one = registry.start("tool", "a_very_long_tool_name_indeed", "with detail")
    spinner._draw("|")
    registry.finish(long_one)
    with registry.track("tool", "x"):
        spinner._draw("|")

    last = stream.getvalue().split("\r")[-2]
    assert "a_very_long_tool_name_indeed" not in last


def test_the_spinner_says_thinking_when_nothing_is_registered():
    coordinator = OutputCoordinator(stream=_Wide())
    spinner = Spinner(coordinator, ActivityRegistry())
    spinner._draw(spinner._frames[0])
    assert "thinking" in coordinator._stream.getvalue()


def test_frames_are_chosen_for_what_the_console_can_render():
    """Braille renders beautifully and is unprintable on the cp1252 console
    this actually runs on."""
    narrow = Spinner(OutputCoordinator(stream=_Narrow()), None)
    wide = Spinner(OutputCoordinator(stream=_Wide()), None)

    assert narrow._frames == Spinner.ASCII_FRAMES
    assert wide._frames == Spinner.UNICODE_FRAMES
    for frame in narrow._frames:
        frame.encode("cp1252")  # must not raise


def test_a_message_never_lands_on_top_of_the_status_line():
    stream = _Wide()
    coordinator = OutputCoordinator(stream=stream)
    spinner = coordinator.attach_spinner(ActivityRegistry())

    spinner._draw(spinner._frames[0])
    coordinator.emit("Here is the answer.")

    written = stream.getvalue()
    assert written.endswith("Here is the answer.\n")
    assert "\r" in written, "the status line should have been erased first"


def test_the_spinner_does_not_paint_over_a_prompt_being_typed_into():
    stream = _Wide()
    coordinator = OutputCoordinator(stream=stream)
    spinner = Spinner(coordinator, ActivityRegistry())

    with coordinator.prompt("> "):
        spinner._draw(spinner._frames[0])

    assert stream.getvalue() == "", "nothing may be drawn over the user's input"


def test_it_does_not_redraw_in_place_when_another_layer_owns_the_line():
    """It must not fight prompt_toolkit for the bottom line -- but standing
    down entirely was the bug that made voice turns silent, so it reports as
    a line instead."""
    stream = _Wide()
    coordinator = OutputCoordinator(stream=stream)
    coordinator.external_prompt_redraw = True

    Spinner(coordinator, ActivityRegistry())._draw("|")

    written = stream.getvalue()
    assert "\r" not in written, "no in-place redraw while another layer owns the line"
    assert written.endswith("\n"), "progress should arrive as a whole line"


def test_the_spinner_starts_and_stops_cleanly():
    coordinator = OutputCoordinator(stream=_Wide())
    spinner = Spinner(coordinator, ActivityRegistry(), interval=0.01)

    assert spinner.start()
    assert not spinner.start(), "must not run twice"
    assert spinner.running
    spinner.stop()
    assert not spinner.running


def test_the_status_line_stops_even_when_the_turn_fails():
    """A spinner left turning after a failure is worse than none."""
    coordinator = OutputCoordinator(stream=_Wide())
    spinner = Spinner(coordinator, ActivityRegistry(), interval=0.01)

    with pytest.raises(RuntimeError):
        with spinner.active():
            raise RuntimeError("the turn fell over")
    assert not spinner.running


# ------------------------------------------------------------- integration


def test_a_tool_call_registers_itself_while_it_runs():
    """The status line can only name the tool if the tool says so."""
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    seen: list[str] = []
    registry = ToolRegistry(PermissionManager())
    registry.register(
        "slow_thing", "waits", {"type": "object", "properties": {"query": {"type": "string"}}},
        "low", "filesystem_read", lambda **kw: seen.append(ACTIVITY.headline()) or {"ok": True},
    )

    result = registry.execute("slow_thing", {"query": "find something"})

    assert result.success
    assert seen and "slow_thing" in seen[0]
    assert "find something" in seen[0], "the status line should say what it is working on"
    assert not ACTIVITY.busy, "the entry must be closed afterwards"


def test_a_failing_tool_still_clears_its_entry():
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager())

    def explode(**kwargs):
        raise RuntimeError("nope")

    registry.register("bad", "fails", {"type": "object", "properties": {}},
                      "low", "filesystem_read", explode)
    registry.execute("bad", {})
    assert not ACTIVITY.busy


# ------------------------------------------------- every path, not just typed


def test_the_spinner_follows_work_from_any_source():
    """Wrapping the typed turn covered typed turns and nothing else. In
    combined mode a spoken request runs through the voice worker, which never
    passes through that wrapper -- so asking out loud produced a silent wait
    while typing the same words showed progress."""
    registry = ActivityRegistry()
    coordinator = OutputCoordinator(stream=_Wide())
    spinner = coordinator.attach_spinner(registry)
    spinner._interval = 0.01
    spinner.follow()

    assert not spinner.running
    activity = registry.start("tool", "web_search", "spoken request")
    try:
        assert spinner.running, "work starting anywhere should show the status line"
    finally:
        registry.finish(activity)
    time.sleep(0.05)
    assert not spinner.running, "it should stop when the work does"


def test_following_survives_a_registry_that_misbehaves():
    coordinator = OutputCoordinator(stream=_Wide())
    spinner = coordinator.attach_spinner(ActivityRegistry())
    spinner.follow()

    class Broken:
        @property
        def busy(self):
            raise RuntimeError("no")

    spinner._on_activity_change(Broken())  # must not raise


def test_a_managed_prompt_gets_progress_lines_instead_of_silence():
    """The combined-mode bug. While the reader blocks on prompt_toolkit,
    external_prompt_redraw is set -- and the spinner used to stand down
    entirely, so a spoken request showed nothing at all while a typed one
    animated."""
    registry = ActivityRegistry()
    stream = _Wide()
    coordinator = OutputCoordinator(stream=stream)
    spinner = coordinator.attach_spinner(registry)
    coordinator.external_prompt_redraw = True

    with registry.track("tool", "web_search", "spoken request"):
        spinner._draw("|")

    written = stream.getvalue()
    assert "web_search" in written, "a managed prompt must still show progress"
    assert "\n" in written, "it should be a line, not an in-place redraw"


def test_progress_lines_are_throttled_so_they_do_not_spam():
    registry = ActivityRegistry()
    stream = _Wide()
    coordinator = OutputCoordinator(stream=stream)
    spinner = coordinator.attach_spinner(registry)
    coordinator.external_prompt_redraw = True

    with registry.track("tool", "web_search"):
        for _ in range(40):
            spinner._draw("|")

    assert stream.getvalue().count("web_search") == 1
