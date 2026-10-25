"""Every download in one place: files, models, videos and web requests, live and in history, and the window that shows them."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.research.fetcher as fetcher_module
from app.transfers import TransferLog, transfers, where


def test_a_download_is_followed_live_then_kept_in_history_across_restarts(tmp_path):
    log = TransferLog(tmp_path / "transfers.jsonl")
    told = []
    log.listeners.append(lambda: told.append(1))
    ticket = log.start("file", "Dataset", "https://github.com/x/data.zip?token=secret", str(tmp_path / "data.zip"), total=1000)
    log.progress(ticket, 400)
    active = log.active()
    assert active[0]["done"] == 400 and active[0]["total"] == 1000 and active[0]["host"] == "github.com"
    assert active[0]["path"] == "/x/data.zip"  # the query, with its token, is never kept
    item = log.finish(ticket, "done", done=1000, destination=str(tmp_path / "data.zip"))
    assert item["status"] == "done" and log.active() == [] and told == [1, 1]
    again = TransferLog(tmp_path / "transfers.jsonl")  # a restart reads the history back
    assert [row["what"] for row in again.history()] == ["Dataset"] and "secret" not in (tmp_path / "transfers.jsonl").read_text()
    assert again.history(kind="model") == [] and log.finish(999) is None


def test_web_requests_are_counted_per_site_without_their_queries():
    log = TransferLog()
    log.web("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", purpose="Markets", size=2000, ms=120)
    log.web("https://api.kraken.com/0/public/OHLC?pair=XBTUSD", purpose="Markets", size=8000, ms=180)
    log.web("https://mempool.space/api/v1/blocks", purpose="Web3", size=0, ms=9000, status="failed", error="ReadTimeout")
    recent = log.web_recent()
    assert recent[0]["host"] == "mempool.space" and recent[0]["status"] == "failed" and "pair" not in recent[1]["path"]
    sites = {site["host"]: site for site in log.sites()}
    assert sites["api.kraken.com"]["requests"] == 2 and sites["api.kraken.com"]["bytes"] == 10000 and sites["api.kraken.com"]["average_ms"] == 150
    assert sites["mempool.space"]["failed"] == 1 and log.summary()["web_requests"] == 3
    assert where("https://a.org/b?c=d") == ("a.org", "/b")


def test_the_public_fetcher_reports_every_request(monkeypatch):
    monkeypatch.setattr(fetcher_module, "validate_public_url", lambda url: url)
    fetcher = fetcher_module.PublicFetcher("HAYAT-Markets/1.0", timeout=2, delay=0, respect_robots=False)
    fetcher.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(404) if "missing" in str(request.url) else
        httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"ok": true}')))
    before = len(transfers().web_recent(500))
    fetcher.fetch("https://api.example.com/prices?key=secret")
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.fetch("https://api.example.com/missing")
    rows = transfers().web_recent(2)
    assert len(transfers().web_recent(500)) >= min(500, before + 2)
    assert rows[0]["status"] == "failed" and rows[1]["purpose"] == "Markets" and rows[1]["bytes"] == 12 and rows[1]["path"] == "/prices"


def test_file_downloads_and_model_pulls_join_the_log(tmp_path, monkeypatch):
    from app.downloads.manager import DownloadManager

    class Response:
        url = "https://github.com/example/data.txt"
        headers = {"content-length": "8"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, _):
            yield b"1234"
            yield b"5678"

    monkeypatch.setattr("app.downloads.manager.httpx.stream", lambda *_, **__: Response())
    manager = DownloadManager(SimpleNamespace(ensure_capacity=lambda _bytes: None), tmp_path / "downloads",
                              scanner=SimpleNamespace(scan=lambda _path: {"clean": True, "status": "clean", "provider": "test"}))
    result = manager.download("https://github.com/example/data.txt", purpose="Test dataset")
    row = transfers().history(1)[0]
    assert row["kind"] == "file" and row["what"] == "Test dataset" and row["destination"] == result["path"] and row["done"] == 8
    blocked = DownloadManager(SimpleNamespace(ensure_capacity=lambda _bytes: None), tmp_path / "blocked",
                              scanner=SimpleNamespace(scan=lambda _path: {"clean": False, "reason": "test detection"}))
    with pytest.raises(PermissionError):
        blocked.download("https://github.com/example/data.txt")
    assert transfers().history(1)[0]["status"] == "blocked"

    from app.models.pull import ModelPulls
    from tests.test_model_pull import FakeHTTP
    done = threading.Event()
    pulls = ModelPulls("http://localhost:11434", http=FakeHTTP(), notify=lambda job, text: done.set())
    pulls.approve("llama3:8b")
    pulls.start("llama3:8b", 1000)
    assert done.wait(3)
    model = next(row for row in transfers().history(5) if row["kind"] == "model")
    assert model["what"] == "llama3:8b" and model["status"] == "done" and model["host"] == "registry.ollama.ai" and model["done"] == 1000


def test_the_window_shows_active_history_and_web_activity(shared_tk, tmp_path, monkeypatch):
    from app.gui_downloads import DownloadsButton, DownloadsWindow
    from app.gui_theme import Palette
    log = transfers()
    file = tmp_path / "report.pdf"
    file.write_bytes(b"x" * 10)
    finished = log.start("video", "https://videos.example.com/clip", "https://videos.example.com/clip", str(tmp_path))
    log.finish(finished, "done", done=10, destination=str(file))
    running = log.start("model", "qwen3:8b", "registry.ollama.ai", "Ollama", total=2000)
    log.progress(running, 500)
    log.web("https://api.kraken.com/0/public/Ticker", purpose="Markets", size=1500, ms=90)
    opened = []
    monkeypatch.setattr("app.gui_downloads.subprocess.Popen", lambda args: opened.append(args))

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(downloads=None)
    window = DownloadsWindow(gui)
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not window.active_tree.get_children():
            shared_tk.update()
            time.sleep(.02)
        progress = window.active_tree.item(str(running), "values")[0]
        assert "25%" in progress and "500 B of 2.0 KB" in progress
        assert any(window.history_tree.item(row, "values")[1] == "https://videos.example.com/clip" for row in window.history_tree.get_children())
        index = next(row for row in window.history_tree.get_children() if window.history_tree.item(row, "values")[3] == str(file))
        window.history_tree.selection_set(index)
        window.history_selected()
        assert "Saved to: " + str(file) in window.history_note.cget("text")
        window.show_in_folder()
        assert opened == [["explorer", "/select,", str(file)]]
        window.tabs.select(2)
        window.refresh_web()
        assert any(window.web_tree.item(row, "values")[1] == "api.kraken.com" for row in window.web_tree.get_children())
        window.filter.set("kraken")
        assert all("kraken" in window.web_tree.item(row, "values")[1] for row in window.web_tree.get_children())
        button = DownloadsButton(window, Palette(), lambda: opened.append("open"))
        button.draw()
        assert any(button.type(item) == "text" for item in button.find_all())  # the badge counts the model still downloading
        button.event_generate("<Button-1>")
        log.finish(running, "cancelled")
        button.draw()
        assert not any(button.type(item) == "text" for item in button.find_all())
    finally:
        window.close()


def test_the_history_file_is_json_lines(tmp_path):
    log = TransferLog(tmp_path / "t.jsonl")
    log.finish(log.start("file", "a", "https://x.org/a"), "done", done=1)
    assert json.loads(Path(tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[0])["what"] == "a"
