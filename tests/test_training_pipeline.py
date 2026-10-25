import sqlite3
from pathlib import Path

import pytest

from app.training.pipeline import TrainingDataPipeline
from app.training.lifecycle import PrivateFineTuneLifecycle


def test_training_pipeline_requires_validation_and_provenance(tmp_path: Path):
    # Explicit config: with none, the pipeline loads the live settings.yaml,
    # so this asserted whatever Mohsin's automatic_training happened to be
    # rather than anything about the pipeline.
    pipeline = TrainingDataPipeline(tmp_path / "training", config={"automatic_training": False})
    with pytest.raises(ValueError):
        pipeline.add("routing", {"input": "hi"}, provenance={"source": "test"}, verified=False)
    with pytest.raises(ValueError):
        pipeline.add("routing", {"input": "hi"}, provenance={}, verified=True)
    destination = pipeline.add("routing", {"input": "hi", "ideal_intent": "CONVERSATION"}, provenance={"source": "verified_test"}, verified=True)
    assert destination.is_file()
    status = pipeline.status()
    assert status["total"] == 1
    assert not status["training_gate"]["ready"]
    assert status["training_gate"]["automatic_training"] is False


def test_private_training_is_consent_gated_local_and_separated(tmp_path: Path):
    pipeline = TrainingDataPipeline(
        tmp_path / "training",
        config={"private_data": {
            "enabled": True, "local_only": True,
            "explicit_consent_required": True, "cloud_export_allowed": False,
        }},
    )
    sample = {
        "input": "Use the owner's private project convention.",
        "ideal_response": "Follow the verified local convention.",
        "api_key": "sk-private-example-123456789",
    }
    with pytest.raises(PermissionError):
        pipeline.ingest(
            "conversation", sample, provenance={"source": "owner_selected"},
            privacy_scope="PRIVATE_LOCAL", owner_consent=False,
        )

    destination = pipeline.add_private(
        "conversation", sample,
        provenance={"source": "owner_selected", "password": "do-not-store-this"},
        verified=True, owner_consent=True,
    )
    assert destination.is_file()
    assert destination.parent.name == "private"
    status = pipeline.status()
    assert status["private_local_records"] == 1
    assert status["private_data"] == {
        "enabled": True, "local_only": True, "explicit_consent_required": True,
        "cloud_export_allowed": False, "credentials_redacted": True,
        "automatic_collection": False, "automatic_training": False,
    }
    assert status["validation"]["passed"]
    exported = destination.read_text(encoding="utf-8")
    assert "PRIVATE_LOCAL" in exported
    assert "sk-private" not in exported and "do-not-store-this" not in exported


def test_private_training_refuses_nonlocal_or_cloud_export_configuration(tmp_path: Path):
    pipeline = TrainingDataPipeline(
        tmp_path / "training",
        config={"private_data": {
            "enabled": True, "local_only": False,
            "explicit_consent_required": True, "cloud_export_allowed": True,
        }},
    )
    with pytest.raises(PermissionError):
        pipeline.ingest(
            "routing", {"input": "private"}, provenance={"source": "owner"},
            privacy_scope="PRIVATE_LOCAL", owner_consent=True,
        )


def test_standard_and_private_corpora_never_merge_duplicates(tmp_path: Path):
    pipeline = TrainingDataPipeline(
        tmp_path / "training",
        config={"private_data": {
            "enabled": True, "local_only": True,
            "explicit_consent_required": True, "cloud_export_allowed": False,
        }},
    )
    sample = {"input": "same", "ideal_response": "same result"}
    pipeline.add("conversation", sample, provenance={"source": "standard"}, verified=True)
    with pytest.raises(ValueError, match="privacy scopes"):
        pipeline.add_private(
            "conversation", sample, provenance={"source": "private"},
            verified=True, owner_consent=True,
        )


def test_private_candidate_stays_blocked_without_private_splits_and_local_checkpoint(tmp_path: Path):
    pipeline = TrainingDataPipeline(
        tmp_path / "training",
        config={"private_data": {
            "enabled": True, "local_only": True,
            "explicit_consent_required": True, "cloud_export_allowed": False,
        }},
    )
    lifecycle = PrivateFineTuneLifecycle(
        pipeline, tmp_path,
        config={
            "minimum_validated_samples": 1000,
            "require_validation_split": True,
            "require_held_out_test": True,
            "training_base_checkpoint": "",
        },
    )
    candidate = lifecycle.prepare(profile="conversation", method="QLORA", private_only=True)
    assert candidate["state"] == "BLOCKED"
    assert candidate["gate"]["records"] == 0
    assert "minimum_samples" in candidate["gate"]["blockers"]
    assert "validation_split" in candidate["gate"]["blockers"]
    assert "held_out_test" in candidate["gate"]["blockers"]
    assert "local_trainable_checkpoint" in candidate["gate"]["blockers"]
    assert lifecycle.status()["automatic_promotion"] is False


def test_candidate_promotion_requires_evaluation_and_owner_approval(tmp_path: Path):
    pipeline = TrainingDataPipeline(tmp_path / "training")
    lifecycle = PrivateFineTuneLifecycle(pipeline, tmp_path, config={})
    candidate = lifecycle.prepare()
    with pytest.raises(RuntimeError):
        lifecycle.promote(candidate["candidate_id"], owner_approved=True)
    with pytest.raises(PermissionError):
        lifecycle.promote(candidate["candidate_id"], owner_approved=False)


# --------------------------------------- what a switch in the config must mean


def test_the_status_reports_the_configuration_rather_than_a_constant(tmp_path):
    """private_status() hardcoded both of these to False whatever the config
    said, so switching them on in settings.yaml changed the file and nothing
    else -- the status kept answering "off" and every caller believed it."""
    from app.training.pipeline import TrainingDataPipeline

    on = TrainingDataPipeline(tmp_path / "on", config={
        "automatic_training": True,
        "private_data": {"enabled": True, "automatic_collection": True},
    })
    assert on.private_status()["automatic_collection"] is True
    assert on.private_status()["automatic_training"] is True

    off = TrainingDataPipeline(tmp_path / "off", config={
        "automatic_training": False,
        "private_data": {"enabled": True, "automatic_collection": False},
    })
    assert off.private_status()["automatic_collection"] is False
    assert off.private_status()["automatic_training"] is False


def test_an_unverified_sample_cannot_enter_the_training_dataset(tmp_path):
    """The rule that stops a model being fitted to its own output."""
    from app.training.pipeline import TrainingDataPipeline

    pipeline = TrainingDataPipeline(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        pipeline.add("conversation", {"input": "q", "response": "a"},
                     provenance={"source": "automatic_collection"}, verified=False)


def test_collected_conversation_waits_as_raw_and_cannot_train(tmp_path):
    """Automatic collection gathers candidates; it does not create training
    data. An answer that passed HAYAT's own completeness check is HAYAT
    grading its own homework, so it lands as RAW and stays out of the gate
    until something else vouches for it."""
    from app.training.pipeline import TrainingDataPipeline

    pipeline = TrainingDataPipeline(tmp_path)
    record = pipeline.ingest("conversation", {"input": "explain b-trees", "response": "..."},
                             provenance={"source": "automatic_collection"})

    assert record["state"] == "RAW"
    status = pipeline.status()
    assert status["states"]["RAW"] == 1
    assert status["eligible"] == 0, "a raw candidate was counted as trainable"


def test_the_assistant_collects_only_answers_that_passed_the_check():
    """Training on replies HAYAT judged incomplete teaches it to be
    incomplete, so the completeness check is the admission test."""
    import inspect

    from app.assistant import AssistantService

    source = inspect.getsource(AssistantService._collect_exchange)
    assert "training.ingest(" in source, "collection must not claim verification"
    assert "training.add(" not in source
    assert "last_assessment" in source and "complete" in source
    assert "_is_about_the_world" in source, "private talk must not become training data"


def test_the_training_gate_reports_the_configured_flag_not_a_constant(tmp_path: Path):
    """The GUI's training panel reads this dict, and it was a literal False.

    Turning automatic_training on in settings.yaml changed the pipeline and
    not the number on screen, so the panel kept reporting "automatic training
    False" over a configuration that said true. A status display that cannot
    report a change is worse than no display at all.
    """
    off = TrainingDataPipeline(tmp_path / "off", config={"automatic_training": False})
    on = TrainingDataPipeline(tmp_path / "on", config={"automatic_training": True})
    assert off.training_gate(0)["automatic_training"] is False
    assert on.training_gate(0)["automatic_training"] is True
    assert on.status()["training_gate"]["automatic_training"] is True
    assert on.private_status()["automatic_training"] is True


def test_an_unreadable_registry_is_quarantined_rather_than_fatal(tmp_path: Path):
    """A destroyed registry stopped the whole training subsystem.

    data/training/registry.db was found with Tk's error output written over
    its header -- "invalid command name ..._tick (after script)" -- from GUI
    callbacks that outlived their interpreter. Every call into training then
    raised, and thirteen tests failed on a database problem that had nothing
    to do with them.

    The learner already quarantines its own corrupt queue, but its check
    looked only for "malformed" and "not a database"; SQLite calls this one
    "unsupported file format", so that precedent would have let it through.
    """
    root = tmp_path / "training"
    pipeline = TrainingDataPipeline(root, config={"automatic_training": False})
    pipeline.add("routing", {"input": "hi", "ideal_intent": "CONVERSATION"},
                 provenance={"source": "verified_test"}, verified=True)
    assert pipeline.status()["total"] == 1

    # Exactly the damage that was found: text over page one.
    registry = root / "registry.db"
    original = bytearray(registry.read_bytes())
    original[24:80] = b'invalid command name "2431568715968_tick" ("after" script)'[:56]
    registry.write_bytes(bytes(original))

    probe = sqlite3.connect(registry)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        # Closed deliberately: Windows will not rename a file that still has
        # an open handle, so leaking this would stop the quarantine under test.
        probe.close()

    # Reopening notices, sets the wreck aside, and carries on.
    rebuilt = TrainingDataPipeline(root, config={"automatic_training": False})
    assert rebuilt.status()["total"] == 0
    quarantined = list(root.glob("registry.corrupt.*.db"))
    assert len(quarantined) == 1, "the damaged file must be kept for inspection"
    assert b"_tick" in quarantined[0].read_bytes()

    # And the rebuilt registry works.
    rebuilt.add("routing", {"input": "again", "ideal_intent": "CONVERSATION"},
                provenance={"source": "verified_test"}, verified=True)
    assert rebuilt.status()["total"] == 1


def test_collected_records_wait_for_review_and_the_owners_decision_moves_them(tmp_path):
    """45 collected records had waited with no way to review them."""
    from app.training.pipeline import TrainingDataPipeline

    pipeline = TrainingDataPipeline(tmp_path, config={})
    first = pipeline.ingest("planning", {"input": "open notepad"}, provenance={"source": "test"})["id"]
    second = pipeline.ingest("planning", {"input": "create an image"}, provenance={"source": "test"})["id"]
    waiting = pipeline.pending_review()
    assert waiting["total"] == 2 and waiting["records"][0]["sample"] == {"input": "open notepad"}

    kept = pipeline.review(first, approved=True)
    assert kept["state"] in {"TRAINING_READY", "HELD_OUT"} and kept["split"]
    dropped = pipeline.review(second, approved=False)
    assert dropped["state"] == "REJECTED"
    assert pipeline.pending_review()["total"] == 0
