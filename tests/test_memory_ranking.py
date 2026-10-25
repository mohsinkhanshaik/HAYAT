"""Recall finds the memory a person means: by acronym, and by the word that matters."""
from __future__ import annotations

from app.linguistics import defined_acronyms, query_acronyms
from app.memory.database import MemoryDatabase


def test_query_acronyms_are_capitalised_or_vowel_free_words():
    assert query_acronyms("Retrieve the verified FSDP procedure") == ("fsdp",)
    assert query_acronyms("compare fsdp with PCIe and KV caches") == ("fsdp", "pcie", "kv")
    # Ordinary words are not acronyms, whatever their case.
    assert query_acronyms("What is the cat doing") == ()
    assert query_acronyms("rdma") == ()          # has a vowel: written lowercase, it stays a word


def test_only_capitalised_runs_define_an_acronym():
    assert "fsdp" in defined_acronyms("Fully Sharded Data Parallel shards parameters")
    assert "rdma" in defined_acronyms("Remote Direct Memory Access moves data")
    assert "pcie" in defined_acronyms("PCI Express connects accelerators")   # all-caps words count whole
    assert "dns" not in defined_acronyms("data never stops")                  # lowercase prose never does
    # Punctuation ends a run: a sentence boundary cannot join two names.
    assert "ab" not in defined_acronyms("Alpha. Beta")


def _database(tmp_path, *contents):
    database = MemoryDatabase(tmp_path / "ranking.db")
    database.initialize()
    return database, [database.store_memory("semantic", text, .6, "user") for text in contents]


def test_an_acronym_finds_the_memory_that_spells_it_out(tmp_path):
    database, (fsdp, _other, _third) = _database(
        tmp_path,
        "Fully Sharded Data Parallel shards parameters gradients and optimizer state across workers.",
        "Distributed training frameworks often shard work across many parallel workers.",
        "Data pipelines should validate inputs before training starts.",
    )
    results = database.retrieve("FSDP", limit=3, strategy="lexical")
    assert results and results[0]["id"] == fsdp


def test_an_acronym_reaches_an_old_memory_outside_the_recent_window(tmp_path, monkeypatch):
    from app.memory import database as database_module

    monkeypatch.setattr(database_module, "load_settings", lambda: {"memory_retrieval": {"candidate_limit": 50}})
    database = MemoryDatabase(tmp_path / "old.db")
    database.initialize()
    target = database.store_memory("semantic", "Remote Direct Memory Access moves data between hosts.", .2, "user")
    for index in range(120):
        database.store_memory("semantic", f"Routine note {index} about lunch plans and errands.", .9, "user")
    results = database.retrieve("What do I know about RDMA?", limit=5, strategy="lexical")
    assert [item["id"] for item in results][:1] == [target]


def test_a_rare_matching_word_outranks_several_common_ones(tmp_path):
    # The second memory matches four query words, but every one of them is in
    # nearly every memory. Counting words equally, it used to win.
    database, (rare, _common) = _database(
        tmp_path,
        "The NCCL library handles collective communication.",
        "The list for the day.",
    )
    for index in range(20):
        database.store_memory("semantic", f"The item {index} is on the list for the day.", .6, "user")
    results = database.retrieve("the NCCL list for the day", limit=3, strategy="lexical")
    assert results[0]["id"] == rare


def test_a_sentence_opening_capital_is_not_treated_as_a_name(tmp_path):
    database, (named, opener) = _database(
        tmp_path,
        "NVLink links accelerators directly.",
        "Which cables were ordered is still unknown.",
    )
    results = database.retrieve("Which NVLink note did I save?", limit=2)
    assert results[0]["id"] == named
    assert results[0]["retrieval"]["entity"] == 1.0
