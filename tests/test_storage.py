
from app.storage.manager import StorageManager


def test_model_metadata_is_parallel_cached_and_invalidated_by_digest(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from app.storage import manager as module
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11435')
    rows = [{"name": f"model-{i}", "digest": f"digest-{i}", "details": {}} for i in range(4)]
    calls, threads, lock, barrier = [], set(), threading.Lock(), threading.Barrier(4)
    def response(data):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
    def get(url, **kwargs):
        assert kwargs['trust_env'] is False
        return response({"models": rows if url.endswith('/api/tags') else []})
    def post(url, **kwargs):
        with lock:
            calls.append(kwargs['json']['model'])
            first_batch = len(calls) <= 4
            threads.add(threading.get_ident())
        if first_batch:
            barrier.wait(timeout=2)
        return response({"capabilities": ['completion'], "model_info": {'test.context_length': 4096}})
    monkeypatch.setattr(module.httpx, 'get', get)
    monkeypatch.setattr(module.httpx, 'post', post)
    storage = StorageManager(project_root=tmp_path)
    assert all(m['context'] == 4096 for m in storage.model_inventory()['models'])
    assert len(threads) == 4
    storage.model_inventory()
    assert len(calls) == 4
    rows[0]['digest'] = 'changed-weights-or-config'
    storage.model_inventory()
    assert len(calls) == 5 and calls[-1] == 'model-0'


def test_concurrent_capacity_checks_share_one_scan(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    (tmp_path / "existing.txt").write_bytes(b"12345")
    manager = StorageManager({"reserve_free_space_bytes": 0}, project_root=tmp_path)
    entered, release, second_started = Event(), Event(), Event()
    scans = []
    original = manager._scan

    def blocked_scan():
        scans.append(1)
        entered.set()
        assert release.wait(3)
        return original()

    def second_check():
        second_started.set()
        manager.ensure_capacity(1)

    monkeypatch.setattr(manager, "_scan", blocked_scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.status)
        try:
            assert entered.wait(2)
            second = pool.submit(second_check)
            assert second_started.wait(2)
        finally:
            release.set()
        assert first.result(timeout=3)["used_bytes"] == 5
        second.result(timeout=3)
    assert len(scans) == 1


def test_a_slow_scan_stays_fresh_after_it_finishes(tmp_path, monkeypatch):
    manager = StorageManager({"reserve_free_space_bytes": 0}, project_root=tmp_path)
    clock = [100.0]
    scans = []
    original = manager._scan

    def slow_scan():
        scans.append(1)
        clock[0] += 90
        return original()

    monkeypatch.setattr("app.storage.manager.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(manager, "_scan", slow_scan)
    assert manager.status()["cache_age_seconds"] == 0
    manager.ensure_capacity(1)
    assert len(scans) == 1


def test_quick_status_keeps_an_old_scan_visible_without_rescanning(tmp_path, monkeypatch):
    manager = StorageManager({"reserve_free_space_bytes": 0}, project_root=tmp_path)
    clock = [100.0]
    monkeypatch.setattr("app.storage.manager.time.monotonic", lambda: clock[0])
    manager.status()
    clock[0] += 4000

    def unexpected_scan():
        raise AssertionError("A quick GUI poll must not walk the project")

    monkeypatch.setattr(manager, "_scan", unexpected_scan)
    assert manager.quick_status()["cache_age_seconds"] == 4000


def test_quick_status_does_not_wait_for_a_background_refresh(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    (tmp_path / "existing.txt").write_bytes(b"12345")
    manager = StorageManager({"reserve_free_space_bytes": 0}, project_root=tmp_path)
    manager.status()
    entered, release = Event(), Event()
    original = manager._scan

    def blocked_scan():
        entered.set()
        assert release.wait(3)
        return original()

    monkeypatch.setattr(manager, "_scan", blocked_scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(manager.status, force_refresh=True)
        try:
            assert entered.wait(2)
            reading = pool.submit(manager.quick_status).result(timeout=1)
            assert reading["used_bytes"] == 5
            # A widget must not be able to corrupt the shared quota reading.
            reading["categories"]["projects"]["used_bytes"] = 99999
            assert manager.quick_status()["used_bytes"] == 5
        finally:
            release.set()
        assert refresh.result(timeout=3)["used_bytes"] == 5


def test_storage_budget_categories_and_reserve(tmp_path):
    (tmp_path / "data/knowledge").mkdir(parents=True)
    (tmp_path / "data/knowledge/knowledge.txt").write_bytes(b"k" * 20)
    (tmp_path / "data/models").mkdir(parents=True)
    (tmp_path / "data/models/model.bin").write_bytes(b"m" * 30)
    manager = StorageManager({"root": str(tmp_path), "maximum_total_bytes": 1000, "reserve_free_space_bytes": 0}, project_root=tmp_path)
    status = manager.status()
    assert status["used_bytes"] == 50
    assert status["remaining_budget_bytes"] == 950
    assert status["categories"]["learning"]["used_bytes"] == 20
    assert status["categories"]["models"]["used_bytes"] == 30


def test_learning_has_its_own_enforced_allocation(tmp_path):
    learning = tmp_path / "data/learning"
    learning.mkdir(parents=True)
    (learning / "facts.db").write_bytes(b"x" * 8)
    manager = StorageManager({
        "root": str(tmp_path), "maximum_total_bytes": 100,
        "reserve_free_space_bytes": 0, "category_quotas": {"learning": 10},
    }, project_root=tmp_path)
    status = manager.status()
    assert status["learning_allocation_bytes"] == 10
    assert status["learning_used_bytes"] == 8
    manager.ensure_capacity(2, "learning")
    try:
        manager.ensure_capacity(3, "learning")
        assert False, "the learning allocation must be enforced"
    except OSError as exc:
        assert "learning allocation" in str(exc)


def test_storage_cleanup_only_removes_disposable_data(tmp_path):
    work = tmp_path / "data/work"; work.mkdir(parents=True)
    disposable = work / "temporary.tmp"; disposable.write_text("temporary")
    labs = work / "labs"; labs.mkdir()
    lab_database = labs / "labs.db"; lab_database.write_bytes(b"durable")
    knowledge = tmp_path / "data/knowledge"; knowledge.mkdir(parents=True)
    protected = knowledge / "important.md"; protected.write_text("keep")
    manager = StorageManager({"root": str(tmp_path), "automatic_cleanup": True, "reserve_free_space_bytes": 0}, project_root=tmp_path)
    result = manager.clean()
    assert result["files_removed"] == 1
    assert not disposable.exists()
    assert lab_database.read_bytes() == b"durable"
    assert protected.read_text() == "keep"


def test_storage_rejects_a_root_outside_its_project(tmp_path):
    outside = tmp_path.parent / "outside-storage"
    try:
        StorageManager({"root": str(outside)}, project_root=tmp_path)
        assert False, "outside storage root must be rejected"
    except ValueError:
        pass


def test_a_relative_root_follows_the_folder_when_it_is_copied(tmp_path):
    """settings.yaml says "." so a copy on another drive or machine starts; it had pinned D:\\AI and refused to."""
    for place in ("D-AI", "E-HAYAT"):
        folder = tmp_path / place
        folder.mkdir()
        assert StorageManager({"root": "."}, project_root=folder).root == folder.resolve()
    try:
        StorageManager({"root": "../elsewhere"}, project_root=tmp_path / "D-AI")
        assert False, "a relative root that leaves the project must still be rejected"
    except ValueError:
        pass
    from app.config import load_yaml
    assert load_yaml("config/settings.yaml")["storage"]["root"] == "."


def test_storage_rejects_write_past_project_budget(tmp_path):
    (tmp_path / "existing.bin").write_bytes(b"12345678")
    manager = StorageManager({"root": str(tmp_path), "maximum_total_bytes": 10, "reserve_free_space_bytes": 0}, project_root=tmp_path)
    try:
        manager.ensure_capacity(3)
        assert False, "capacity beyond project quota must be rejected"
    except OSError as exc:
        assert "project budget" in str(exc)


def test_storage_status_caches_between_calls_within_max_age(tmp_path, monkeypatch):
    """Regression test: status() used to re-walk the whole project tree on
    every call, including from paths that only needed a static config value
    (e.g. observability.system() reading maximum_total_bytes). That made a
    routine world-state snapshot pay for a full filesystem scan every time."""
    (tmp_path / "data/knowledge").mkdir(parents=True)
    (tmp_path / "data/knowledge/file.txt").write_bytes(b"x" * 10)
    manager = StorageManager({"root": str(tmp_path), "reserve_free_space_bytes": 0}, project_root=tmp_path)

    scan_calls = []
    original_scan = manager._scan

    def counting_scan():
        scan_calls.append(1)
        return original_scan()

    monkeypatch.setattr(manager, "_scan", counting_scan)

    first = manager.status(max_age_seconds=60)
    second = manager.status(max_age_seconds=60)
    assert len(scan_calls) == 1, "second call within max_age must reuse the cache, not rescan"
    assert first["used_bytes"] == second["used_bytes"] == 10
    assert second["last_scanned_at"] == first["last_scanned_at"]

    forced = manager.status(max_age_seconds=60, force_refresh=True)
    assert len(scan_calls) == 2, "force_refresh must bypass the cache"
    assert forced["used_bytes"] == 10

    manager.status(max_age_seconds=0)
    assert len(scan_calls) == 3, "a max_age of 0 must always be treated as stale"


def test_storage_scan_matches_manual_walk(tmp_path):
    """The os.scandir-based walk must find exactly the same files as a naive
    walk, including nested directories and excluding symlinks."""
    nested = tmp_path / "data/knowledge/nested/deeper"
    nested.mkdir(parents=True)
    (nested / "leaf.txt").write_bytes(b"leaf")
    (tmp_path / "data/knowledge/top.txt").write_bytes(b"top")
    manager = StorageManager({"root": str(tmp_path), "reserve_free_space_bytes": 0}, project_root=tmp_path)
    found = {str(path) for path in manager._files()}
    assert str(nested / "leaf.txt") in found
    assert str(tmp_path / "data/knowledge/top.txt") in found
    assert len(found) == 2
