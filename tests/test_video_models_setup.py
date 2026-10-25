"""Exercise real disk/resume/integrity behavior with bounded HTTP test streams."""
from contextlib import contextmanager
import hashlib
import json

import httpx
import pytest

from scripts import setup_video_models as setup


PAYLOAD = b"0123456789abcdef0123456789abcdef"


class Transfer(httpx.SyncByteStream):
    def __init__(self, data, *, fail_after=None):
        self.data = data
        self.fail_after = fail_after

    def __iter__(self):
        for offset in range(0, len(self.data), 4):
            if self.fail_after is not None and offset >= self.fail_after:
                raise httpx.ReadTimeout("Test transfer interrupted")
            yield self.data[offset:offset + 4]


@pytest.fixture
def pinned(monkeypatch):
    models = {key: {**value, "bytes": len(PAYLOAD),
                    "sha256": hashlib.sha256(PAYLOAD).hexdigest()}
              for key, value in setup.MOTION_MODELS.items()}
    monkeypatch.setattr(setup, "MOTION_MODELS", models)
    monkeypatch.setattr(setup, "CHUNK_BYTES", 4)
    return models


def _response(data=PAYLOAD, status=200, headers=None, fail_after=None):
    return httpx.Response(status, headers=headers or {},
                          stream=Transfer(data, fail_after=fail_after))


@contextmanager
def _client(handler):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        yield client


def _interrupted(root, *, prefix=8):
    with _client(lambda request: _response(headers={"etag": '"model-v1"'}, fail_after=prefix)) as client:
        with pytest.raises(httpx.ReadTimeout):
            setup.install_motion_model("v3", root, client=client)
    target = setup.motion_model_path("v3", root)
    partial = target.with_name(f"{target.name}.partial")
    metadata = partial.with_name(f"{partial.name}.json")
    assert partial.read_bytes() == PAYLOAD[:prefix]
    assert not target.exists()
    return target, partial, metadata


def test_default_v2_and_optional_v3_are_separate_and_existing_files_survive(tmp_path, pinned):
    v2 = setup.motion_model_path(root=tmp_path)
    v2.write_bytes(PAYLOAD)
    observed = []
    with _client(lambda request: _response(headers={"content-length": str(len(PAYLOAD))})) as client:
        target = setup.install_motion_model("v3", tmp_path, client=client,
                                            progress=lambda done, total: observed.append((done, total)))
        assert not client.is_closed
    assert target == tmp_path / "optional" / "v3_sd15_mm.ckpt"
    assert target.read_bytes() == PAYLOAD
    assert v2.read_bytes() == PAYLOAD
    assert observed[0] == (0, len(PAYLOAD))
    assert observed[-1] == (len(PAYLOAD), len(PAYLOAD))
    record = json.loads(target.with_name(f"{target.name}.provenance.json").read_text())
    assert record["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert record["license"] == "Apache-2.0"
    assert record["requires_manual_selection"] is True
    assert record["source_url"] == pinned["v3"]["url"]
    assert not list(target.parent.glob("*.partial*"))


def test_verified_existing_file_needs_no_network(tmp_path, pinned):
    target = setup.motion_model_path(root=tmp_path)
    target.write_bytes(PAYLOAD)
    with _client(lambda _: pytest.fail("Existing verified model must not use the network")) as client:
        assert setup.install_motion_model(root=tmp_path, client=client) == target


@pytest.mark.parametrize("existing", [b"different", b"x" * len(PAYLOAD)])
def test_existing_different_checkpoint_is_never_replaced(tmp_path, pinned, existing):
    target = setup.motion_model_path(root=tmp_path)
    target.write_bytes(existing)
    with _client(lambda _: pytest.fail("Preserve different checkpoint before requesting data")) as client:
        with pytest.raises(FileExistsError, match="different file"):
            setup.install_motion_model(root=tmp_path, client=client)
    assert target.read_bytes() == existing


def test_timeout_is_resumed_with_exact_range_and_validator(tmp_path, pinned):
    target, partial, metadata = _interrupted(tmp_path)
    seen = []

    def resume(request):
        seen.append(dict(request.headers))
        assert request.headers["range"] == "bytes=8-"
        assert request.headers["if-range"] == '"model-v1"'
        assert request.headers["accept-encoding"] == "identity"
        return _response(PAYLOAD[8:], 206, {"content-range": "bytes 8-31/32", "content-length": "24"})

    with _client(resume) as client:
        assert setup.install_motion_model("v3", tmp_path, client=client) == target
    assert target.read_bytes() == PAYLOAD
    assert len(seen) == 1
    assert not partial.exists() and not metadata.exists()


def test_server_ignoring_range_restarts_instead_of_appending(tmp_path, pinned):
    target, _, _ = _interrupted(tmp_path)

    def restart(request):
        assert request.headers["range"] == "bytes=8-"
        return _response()

    with _client(restart) as client:
        setup.install_motion_model("v3", tmp_path, client=client)
    assert target.read_bytes() == PAYLOAD


@pytest.mark.parametrize("headers", [
    {}, {"content-range": "bytes 0-23/32"}, {"content-range": "bytes 8-31/99"},
    {"content-range": "bytes 8-32/32"}, {"content-range": "bytes nonsense"},
    {"content-range": "bytes 8-31/32", "content-length": "32"},
])
def test_wrong_resume_range_is_rejected_without_damaging_prefix(tmp_path, pinned, headers):
    target, partial, metadata = _interrupted(tmp_path)
    before = metadata.read_bytes()
    with _client(lambda _: _response(PAYLOAD[8:], 206, headers)) as client:
        with pytest.raises(setup.DownloadError, match="Range|range"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert partial.read_bytes() == PAYLOAD[:8]
    assert metadata.read_bytes() == before
    assert not target.exists()


def test_corrupt_resumed_prefix_is_detected_before_publication(tmp_path, pinned):
    target, partial, metadata = _interrupted(tmp_path)
    partial.write_bytes(b"badbytes")
    with _client(lambda _: _response(PAYLOAD[8:], 206, {"content-range": "bytes 8-31/32"})) as client:
        with pytest.raises(setup.DownloadError, match="Checksum"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert not target.exists()
    assert not partial.exists() and not metadata.exists()


def test_complete_partial_is_verified_and_published_without_network(tmp_path, pinned):
    target, partial, _ = _interrupted(tmp_path)
    partial.write_bytes(PAYLOAD)
    with _client(lambda _: pytest.fail("Complete partial should only need verification")) as client:
        assert setup.install_motion_model("v3", tmp_path, client=client) == target
    assert target.read_bytes() == PAYLOAD


def test_fully_downloaded_wrong_hash_is_discarded(tmp_path, pinned):
    with _client(lambda _: _response(b"x" * len(PAYLOAD))) as client:
        with pytest.raises(setup.DownloadError, match="Checksum"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert not setup.motion_model_path("v3", tmp_path).exists()
    assert not list((tmp_path / "optional").glob("*.partial*"))


def test_download_cannot_exceed_pinned_size(tmp_path, pinned):
    with _client(lambda _: _response(PAYLOAD + b"overflow")) as client:
        with pytest.raises(setup.DownloadError, match="exceeded"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert not setup.motion_model_path("v3", tmp_path).exists()
    assert not list((tmp_path / "optional").glob("*.partial*"))


def test_incomplete_clean_eof_is_retained_for_resume(tmp_path, pinned):
    with _client(lambda _: _response(PAYLOAD[:8])) as client:
        with pytest.raises(setup.DownloadError, match="partial retained"):
            setup.install_motion_model("v3", tmp_path, client=client)
    target = setup.motion_model_path("v3", tmp_path)
    assert not target.exists()
    assert target.with_name(f"{target.name}.partial").read_bytes() == PAYLOAD[:8]


def test_partial_with_different_pin_is_preserved_without_network(tmp_path, pinned):
    target, partial, metadata = _interrupted(tmp_path)
    saved = json.loads(metadata.read_text())
    saved["sha256"] = "0" * 64
    metadata.write_text(json.dumps(saved))
    with _client(lambda _: pytest.fail("Mismatching partial metadata must not trigger a request")) as client:
        with pytest.raises(setup.DownloadError, match="different pin"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert partial.read_bytes() == PAYLOAD[:8]
    assert json.loads(metadata.read_text()) == saved
    assert not target.exists()


def test_http_failure_keeps_recoverable_prefix(tmp_path, pinned):
    target, partial, metadata = _interrupted(tmp_path)
    with _client(lambda _: _response(b"unavailable", 503)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert partial.read_bytes() == PAYLOAD[:8]
    assert metadata.exists() and not target.exists()


def test_concurrent_installer_cannot_mutate_same_partial(tmp_path, pinned):
    target = setup.motion_model_path("v3", tmp_path)
    target.parent.mkdir()
    with setup._install_lock(target.with_name(f"{target.name}.lock")):
        with _client(lambda _: pytest.fail("Locked installer must not download")) as client:
            with pytest.raises(setup.DownloadError, match="Another installer"):
                setup.install_motion_model("v3", tmp_path, client=client)
    with _client(lambda _: _response()) as client:
        assert setup.install_motion_model("v3", tmp_path, client=client) == target


def test_atomic_publication_does_not_clobber_file_created_during_download(tmp_path, pinned):
    target = setup.motion_model_path("v3", tmp_path)

    def raced(request):
        target.write_bytes(b"created by another program")
        return _response()

    with _client(raced) as client:
        with pytest.raises(FileExistsError, match="different file"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert target.read_bytes() == b"created by another program"


def test_unknown_module_and_unpinned_host_do_not_create_files(tmp_path, pinned):
    with pytest.raises(ValueError, match="Unknown motion"):
        setup.install_motion_model("unknown", tmp_path)
    pinned["v3"]["url"] = "http://huggingface.co/guoyww/animatediff/model"
    with pytest.raises(setup.DownloadError, match="HTTPS"):
        setup.install_motion_model("v3", tmp_path)
    assert not list(tmp_path.iterdir())


def test_https_downgrade_redirect_is_rejected(tmp_path, pinned):
    def redirect(request):
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://cdn.example/model"})
        return _response()

    with _client(redirect) as client:
        with pytest.raises(setup.DownloadError, match="away from HTTPS"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert not setup.motion_model_path("v3", tmp_path).exists()


def test_compressed_download_is_rejected_before_partial_write(tmp_path, pinned):
    with _client(lambda _: _response(headers={"content-encoding": "gzip"})) as client:
        with pytest.raises(setup.DownloadError, match="Compressed transfer"):
            setup.install_motion_model("v3", tmp_path, client=client)
    assert not list((tmp_path / "optional").glob("*.partial*"))


def test_large_response_uses_bounded_chunks_without_reading_whole_file(tmp_path, monkeypatch, pinned):
    import builtins

    payload = b"bounded memory\n" * 150_000
    pinned["v3"]["bytes"] = len(payload)
    pinned["v3"]["sha256"] = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(setup, "CHUNK_BYTES", 64 * 1024)
    original_open = builtins.open
    sizes = []

    def progress(done, total):
        sizes.append(done)
        assert total == len(payload)

    # The installer may read metadata, but must not call Path.read_bytes on any
    # checkpoint or accumulate a body via response.read().
    monkeypatch.setattr(setup.Path, "read_bytes", lambda _: pytest.fail("No whole-file reads"))
    with _client(lambda _: _response(payload)) as client:
        target = setup.install_motion_model("v3", tmp_path, client=client, progress=progress)
    with original_open(target, "rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == pinned["v3"]["sha256"]
    assert max(b - a for a, b in zip(sizes, sizes[1:])) <= 64 * 1024


def test_cli_defaults_to_v2_and_reports_optional_selection(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(setup.sys, "argv", ["setup_video_models.py"])
    monkeypatch.setattr(setup, "install_motion_model", lambda module: seen.append(module) or tmp_path / "v2.ckpt")
    monkeypatch.setattr(setup, "_check_sd15_checkpoint", lambda: None)
    assert setup.main() == 0
    assert seen == ["v2"]
    assert "Verified motion module" in capsys.readouterr().out
    monkeypatch.setattr(setup.sys, "argv", ["setup_video_models.py", "--module", "v3"])
    assert setup.main() == 0
    assert seen == ["v2", "v3"]
    assert "requires explicit selection" in capsys.readouterr().out


def test_cli_network_failure_is_clear_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "argv", ["setup_video_models.py", "--module", "v3"])

    def unavailable(module):
        raise httpx.ConnectError("Connection unavailable; partial retained")

    monkeypatch.setattr(setup, "install_motion_model", unavailable)
    with pytest.raises(SystemExit) as error:
        setup.main()
    assert error.value.code == 1
    assert "Connection unavailable" in capsys.readouterr().err
