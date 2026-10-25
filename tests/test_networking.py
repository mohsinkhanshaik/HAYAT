from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

from app.networking import NetworkingBook
from app.tools.networking import TOOL_NAMES, register_networking_tools


def codec(data, *, decrypt=False):
    """Reversible non-production fixture; never used by default storage."""
    if decrypt:
        assert data.startswith(b"fixture:")
        return base64.b64decode(data[8:])
    return b"fixture:" + base64.b64encode(data)


@pytest.fixture
def book(tmp_path):
    return NetworkingBook(tmp_path / "private", codec=codec)


def test_save_reload_update_preserves_id_and_creation_time(book):
    row = book.save("Ada Example", organization="Example Labs", role="Researcher",
                    expertise="Python, Optimization, python", goals=["open research"],
                    source_url="https://example.org/team/ada#bio", notes="Met at the public workshop.",
                    follow_up="2026-11-03")
    assert row["expertise"] == ["Python", "Optimization"]
    assert row["source_url"] == "https://example.org/team/ada"
    assert "not independently verified" in row["basis"]
    assert row["source_recorded_at"].endswith("+00:00")
    reloaded = NetworkingBook(book.directory, codec=codec)
    assert reloaded.get(row["id"]) == row
    changed = reloaded.save("Ada Example", role="Engineer", contact_id=row["id"])
    assert changed["created_at"] == row["created_at"]
    assert changed["id"] == row["id"]
    assert changed["organization"] == ""  # Full editable-field replacement, not an accidental merge.
    assert len(reloaded.all()) == 1
    assert b"Ada Example" not in book.path.read_bytes()


def test_namesakes_are_separate_until_owner_explicitly_selects_id(book):
    first = book.save("Alex Example", organization="First Lab")
    second = book.save("Alex Example", organization="Second Lab")
    assert first["id"] != second["id"]
    assert len(book.all(query="alex example")) == 2
    assert book.get(first["id"])["organization"] == "First Lab"


def test_channel_provenance_consent_validation_and_deduplication(book):
    row = book.save("Ada Example", source_url="https://example.org/team/ada", channels=[
        {"kind": "email", "value": "ada@example.org", "status": "public_professional"},
        {"kind": "email", "value": "ada@example.org", "status": "public_professional"},
        {"kind": "phone", "value": "+1 202 555 0123", "status": "consent", "consent": True},
        {"kind": "profile", "value": "https://example.org/ada", "status": "provided"},
    ])
    assert len(row["channels"]) == 3
    assert row["channels"][0]["source_url"] == "https://example.org/team/ada"
    assert row["channels"][1]["value"] == "+12025550123"
    assert row["channels"][1]["consent"] is True
    assert all(item["recorded_at"] for item in row["channels"])
    with pytest.raises(PermissionError, match="public source URL"):
        book.save("No source", channels=[{"kind": "email", "value": "a@example.org", "status": "public_professional"}])
    with pytest.raises(PermissionError, match="explicit consent"):
        book.save("No consent", channels=[{"kind": "phone", "value": "+12025550123", "status": "consent"}])
    with pytest.raises(ValueError, match="country code"):
        book.save("Local phone", channels=[{"kind": "phone", "value": "2025550123"}])


@pytest.mark.parametrize("url", [
    "file:///C:/private.txt", "javascript:alert(1)", "https://user:pass@example.org/",
    "http://127.0.0.1/", "http://192.168.1.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://localhost/", "http://device.local/", "https://example.org:444/", "not a url",
    "https://example.org/a b", "https://example.org\\evil/",
])
def test_nonpublic_or_credential_urls_are_rejected_without_fetching(book, url):
    with pytest.raises(ValueError, match="public HTTP"):
        book.save("Example", source_url=url)
    assert book.all() == []


@pytest.mark.parametrize("changes", [
    {"name": ""}, {"name": "x\x00y"}, {"name": "x" * 161},
    {"follow_up": "2026-02-30"}, {"follow_up": "20261103"},
    {"expertise": ["x"] * 41}, {"expertise": {}}, {"goals": False},
    {"channels": {}}, {"channels": [{"kind": "address", "value": "123 Private St"}]},
    {"channels": [{"kind": "email", "value": "x@example.org", "confidence": "verified"}]},
])
def test_invalid_record_is_not_written(book, changes):
    with pytest.raises(ValueError):
        book.save(**{"name": "Example", **changes})
    assert book.all() == []


def test_matching_is_explained_topic_overlap_without_invented_potential(book):
    unrelated = book.save("Unrelated Person", notes="Python AI expert", role="Python researcher")
    match = book.save("Relevant Person", expertise=["Python", "optimization"], goals=["open research"])
    result = book.match("Python optimization research")
    assert [item["id"] for item in result] == [match["id"]]
    evidence = result[0]["relevance"]
    assert evidence["score"] == 100
    assert evidence["matched_expertise"] == ["Python", "optimization"]
    assert evidence["evidence_status"] == "owner_entered_unverified"
    assert "not a probability" in evidence["explanation"]
    assert book.all(goal="Python")[1]["id"] == unrelated["id"]
    assert book.match("quantum chemistry") == []
    assert len(book.all(query="relevant person")) == 1


def test_matching_counterfactual_identity_and_demographic_proxies_do_not_change_score(book):
    first = book.save("Amina Example", organization="Famous Institution", role="Senior Director",
                      expertise=["Python", "Optimization"], goals=["open research"],
                      notes="Owner-entered note: woman, age 58, born in country A.")
    second = book.save("John Example", organization="Independent", role="Student",
                       expertise=["Python", "Optimization"], goals=["open research"],
                       notes="Owner-entered note: man, age 24, born in country B.")
    ranked = {row["id"]: row["relevance"] for row in book.match("Python optimization research")}
    assert ranked[first["id"]] == ranked[second["id"]]
    assert ranked[first["id"]]["score"] == 100
    assert book.match("Senior Director Famous Institution woman country") == []


@pytest.mark.parametrize("limit", [0, 101, True, "10"])
def test_match_limit_validation(book, limit):
    with pytest.raises(ValueError, match="limit"):
        book.match("Python", limit=limit)


def test_draft_uses_only_entered_facts_and_never_claims_sent(book):
    row = book.save("Ada Example", expertise=["Optimization"], channels=[
        {"kind": "email", "value": "ada@example.org", "status": "provided"}])
    draft = book.draft(row["id"], "optimization research", sender="Owner", shared_context="We met at the workshop.")
    assert draft["sent"] is False and draft["review_required"] is True
    assert "We met at the workshop." in draft["body"]
    assert "your work on Optimization" in draft["body"]
    assert "Please feel free to decline" in draft["body"]
    assert "verify identity and channels" in draft["basis"]
    assert book.audit()[-1]["action"] == "contact_saved"  # Draft writes/sends nothing.
    other = book.draft(row["id"], "quantum chemistry")
    assert "your work on" not in other["body"]
    assert "We met" not in other["body"]


def test_revoke_excludes_contact_details_everywhere_but_can_delete(book):
    row = book.save("Ada Example", expertise=["Python"], channels=[{"kind": "email", "value": "ada@example.org"}])
    assert book.revoke(row["id"])["revoked"] is True
    assert book.all() == []
    assert book.match("Python") == []
    redacted = book.all(include_revoked=True)
    assert redacted[0]["name"] == "Revoked contact"
    assert "Ada" not in json.dumps(redacted) and "@" not in json.dumps(redacted)
    assert book.all(query="Ada", include_revoked=True) == []
    for operation in [lambda: book.get(row["id"]), lambda: book.draft(row["id"], "Python"),
                      lambda: book.save("Ada", contact_id=row["id"]), lambda: book.export(row["id"])]:
        with pytest.raises(PermissionError, match="revoked"):
            operation()
    assert book.export(plaintext=True)["records"] == []
    assert book.delete(row["id"]) is True
    assert book.delete(row["id"]) is False
    assert "Ada Example" not in codec(book.path.read_bytes(), decrypt=True).decode()


def test_exports_encrypted_by_default_and_plaintext_is_explicit(book):
    row = book.save("Ada Example", notes="Owner's private note")
    exported = book.export()
    assert exported["encrypted"] is True
    assert "Ada Example" not in json.dumps(exported)
    decoded = json.loads(codec(base64.b64decode(exported["ciphertext_b64"]), decrypt=True))
    assert decoded["records"][0]["id"] == row["id"]
    assert "sends nothing" in decoded["warning"]
    clear = book.export(row["id"], plaintext=True)
    assert clear["encrypted"] is False and clear["records"][0]["name"] == "Ada Example"
    assert "Sensitive local" in clear["warning"]
    with pytest.raises(ValueError, match="explicitly"):
        book.export(plaintext="true")
    assert [item["action"] for item in book.audit()] == ["contact_saved", "encrypted_export", "plaintext_export"]
    assert "Ada Example" not in json.dumps(book.audit())


def test_encryption_failure_never_creates_plaintext_or_replaces_existing(book):
    book.save("Ada Example")
    before = book.path.read_bytes()
    def broken(data, *, decrypt=False):
        if decrypt:
            return codec(data, decrypt=True)
        raise RuntimeError("Encryption unavailable")
    failing = NetworkingBook(book.directory, codec=broken)
    with pytest.raises(RuntimeError, match="Encryption unavailable"):
        failing.save("New person")
    assert book.path.read_bytes() == before
    assert not list(book.directory.glob("*.tmp"))


def test_corrupt_state_is_not_silently_overwritten(book):
    book.directory.mkdir(parents=True)
    bad = codec(b'{"version":9,"records":{}}')
    book.path.write_bytes(bad)
    with pytest.raises(ValueError, match="format"):
        book.save("Do not overwrite")
    assert book.path.read_bytes() == bad


def test_concurrent_book_instances_do_not_lose_contacts(book):
    def save(index):
        NetworkingBook(book.directory, codec=codec).save(f"Contact {index}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(40)))
    assert len(book.all()) == 40


def test_invalid_ids_cannot_read_or_delete_paths(book):
    for bad in ["../private", "x" * 32, "", 123]:
        with pytest.raises(ValueError):
            book.get(bad)
        with pytest.raises(ValueError):
            book.delete(bad)
    with pytest.raises(PermissionError, match="local owner"):
        NetworkingBook(book.directory, principal="remote_user", codec=codec)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI requires a Windows account")
def test_real_windows_account_encryption_roundtrip(tmp_path):
    book = NetworkingBook(tmp_path / "real-dpapi")
    saved = book.save("Windows encryption sample", notes="Sensitive example note")
    assert b"Sensitive example note" not in book.path.read_bytes()
    assert NetworkingBook(book.directory).get(saved["id"])["notes"] == "Sensitive example note"
    assert book.export()["encrypted"] is True


def test_tool_registration_is_real_and_has_no_network_or_send_permissions(book):
    class Registry:
        def __init__(self):
            self.calls = {}
        def register(self, name, description, schema, risk, permission, handler):
            self.calls[name] = {"description": description, "schema": schema, "risk": risk,
                                "permission": permission, "handler": handler}
    registry = Registry()
    register_networking_tools(registry, book)
    assert set(registry.calls) == set(TOOL_NAMES)
    assert all(item["permission"] in {"filesystem_read", "filesystem_write_project"} for item in registry.calls.values())
    row = registry.calls["networking_save"]["handler"](name="Example", expertise=["Python"])
    assert registry.calls["networking_match"]["handler"]("Python")["contacts"][0]["id"] == row["id"]
    assert registry.calls["networking_draft"]["handler"](row["id"], "Python")["sent"] is False


def test_a_name_or_short_id_chooses_a_contact_and_namesakes_are_never_guessed(book):
    ada = book.save("Ada Example", organization="Example Lab", expertise=["Python"])
    assert book.get("Ada Example")["id"] == ada["id"] and book.get("ada example")["id"] == ada["id"]
    assert book.get(ada["id"][:8])["id"] == ada["id"]
    draft = book.draft("Ada Example", "documentation")
    assert draft["contact_id"] == ada["id"] and draft["sent"] is False
    book.save("Ada Example", organization="Second Lab")
    with pytest.raises(ValueError, match="More than one saved contact is named") as found:
        book.draft("Ada Example", "documentation")
    assert "Example Lab" in str(found.value) and "Second Lab" in str(found.value)
    with pytest.raises(ValueError, match="No saved contact is named"):
        book.get("Nobody Here")
    book.revoke(ada["id"])
    assert book.get("Ada Example")["organization"] == "Second Lab"  # a revoked record is not a candidate


def test_an_export_restores_into_a_fresh_book_without_touching_saved_contacts(book, tmp_path):
    book.save("Ada Example", organization="Example Lab", expertise=["Python"],
              channels=[{"kind": "email", "value": "ada@example.org", "status": "consent", "consent": True}])
    book.save("Lin Example", organization="Docs Guild")
    envelope = book.export()
    fresh = NetworkingBook(tmp_path / "fresh", codec=codec)
    fresh.save("Lin Example", organization="Docs Guild", notes="kept as it was")
    result = fresh.restore(envelope)
    assert result["added"] == ["Ada Example"] and result["skipped_existing"] == ["Lin Example"]
    restored = fresh.get("Ada Example")
    assert restored["channels"][0]["value"] == "ada@example.org" and restored["channels"][0]["consent"] is True
    assert fresh.get("Lin Example")["notes"] == "kept as it was"
    assert fresh.restore(envelope)["restored"] == 0  # running it twice adds nothing
    with pytest.raises(ValueError, match="not a HAYAT networking export"):
        fresh.restore({"format": "something-else"})


def test_restore_reads_an_export_file_through_the_tool(book, tmp_path):
    import json

    from app.tools.networking import restore_file

    book.save("Ada Example", organization="Example Lab")
    path = tmp_path / "export.json"
    path.write_text(json.dumps(book.export()), encoding="utf-8")
    fresh = NetworkingBook(tmp_path / "fresh", codec=codec)
    assert restore_file(fresh, str(path))["added"] == ["Ada Example"]
    with pytest.raises(FileNotFoundError):
        restore_file(fresh, str(tmp_path / "missing.json"))
