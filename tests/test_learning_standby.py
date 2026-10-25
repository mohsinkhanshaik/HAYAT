import threading
import time

from app.learning.continuous import ContinuousLearner, CycleResult, LearningBudget


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


def test_standby_keeps_worker_alive_without_exceeding_or_resetting_budget(tmp_path):
    learner = ContinuousLearner(root=tmp_path, standby_after_budget=True,
        budget=LearningBudget(max_cycles_per_hour=1),
        cycle=lambda topic: CycleResult(action="index", ok=True))
    learner.start(topics=lambda: "topic", interval=.01)
    try:
        assert wait_for(lambda: learner.worker_state == "waiting_budget")
        assert learner.running
        assert learner.ledger.cycles_this_hour == 1
        assert not learner.run_cycle("another").ok
        assert learner.ledger.cycles_this_hour == 1
        # Simulate only the passage of the normal accounting window.
        learner.ledger.hour_started -= 3601
        learner._wake.set()
        assert wait_for(lambda: learner.ledger.cycles == 2)
        assert learner.ledger.cycles_this_hour == 1
    finally:
        assert learner.stop(timeout=3)


def test_stop_file_still_ends_standby_without_auto_restart(tmp_path):
    learner = ContinuousLearner(root=tmp_path, standby_after_budget=True,
                               budget=LearningBudget(max_cycles_per_hour=0))
    learner.start(interval=.01)
    assert wait_for(lambda: learner.worker_state == "waiting_budget")
    learner.write_stop_file("owner halt")
    learner._wake.set()
    assert wait_for(lambda: not learner.running)
    assert learner.halt_file_present()


def test_topic_failure_enters_backoff_and_can_recover(tmp_path):
    attempts = []
    def topic():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("temporary queue failure")
        return "topic"
    learner = ContinuousLearner(root=tmp_path, cycle=lambda topic: CycleResult(action="index", ok=True))
    learner.start(topics=topic, interval=.01)
    try:
        assert wait_for(lambda: learner.worker_state == "error_backoff")
        assert learner.running
        learner._wake.set()
        assert wait_for(lambda: learner.ledger.cycles > 0)
    finally:
        learner.stop(timeout=3)


def test_foreground_learn_next_cannot_overlap_background_cycle(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def cycle(topic):
        entered.set()
        release.wait(3)
        return CycleResult(action="index", ok=True)
    learner = ContinuousLearner(root=tmp_path, cycle=cycle)
    worker = threading.Thread(target=learner.run_cycle, args=("first",))
    worker.start()
    try:
        assert entered.wait(2)
        result = learner.run_cycle("second")
        assert not result.ok and "another learning cycle" in result.detail
    finally:
        release.set()
        worker.join(3)
    assert learner.ledger.cycles == 1
