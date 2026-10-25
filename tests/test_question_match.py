from app.question_match import compare_questions
import pytest


def test_reworded_same_question_matches():
    match = compare_questions(
        "common failure modes and diagnostic signals for SQLite WAL durability",
        "What failures occur in SQLite WAL durability and how can I diagnose them?",
    )
    assert match.same and match.score >= 0.7


def test_same_subject_different_question_does_not_reuse_prefetch():
    match = compare_questions(
        "How does SQLite WAL durability work internally?",
        "What are SQLite WAL durability security vulnerabilities?",
    )
    assert not match.same
    assert match.reason == "same subject but a different question"


def test_shared_generic_word_does_not_make_questions_equal():
    assert not compare_questions(
        "How does GPU scheduling work in Kubernetes?",
        "How does GPU image generation work in Stable Diffusion?",
    ).same


def test_acronym_and_long_form_question_can_match():
    assert compare_questions(
        "Why does push to talk latency increase?",
        "Why is PTT latency getting slower?",
    ).same


def test_requirements_are_a_distinct_question_facet():
    match = compare_questions(
        "JSON canonicalization architecture overview",
        "What requirements must JSON canonicalization follow?",
    )
    assert not match.same
    assert "requirements" in match.actual_facets


@pytest.mark.parametrize('first,second', [
    ('What is the probability of rolling a 2 on a standard six sided die?',
     'What is the probability of rolling a 7 on a standard six sided die?'),
    ('What is the ratio of 2 to 7 for the sample distribution?',
     'What is the ratio of 7 to 2 for the sample distribution?'),
    ('What is the temperature change from -5 to 10 degrees?',
     'What is the temperature change from 5 to 10 degrees?'),
    ('What is the distance traveled in 5 seconds at a constant speed?',
     'What is the distance traveled in 5 hours at a constant speed?'),
    ('What is the sum of integers divisible by 3 or 5?',
     'What is the sum of integers divisible by 3 and 5?'),
    ('Is it safe to delete the backup after verifying the archive?',
     'Is it safe to delete the backup before verifying the archive?'),
    ('Can a process access shared memory without holding the lock?',
     'Can a process access shared memory while holding the lock?'),
    ('Does Alice trust Bob?', 'Does Bob trust Alice?'),
    ('What happens when every worker has acquired a shared lock?',
     'What happens when some worker has acquired a shared lock?'),
    ('What is the probability of rolling two sixes?',
     'What is the probability of rolling three sixes?'),
    ('What is the meaning of "A" in the protocol specification?',
     'What is the meaning of "B" in the protocol specification?'),
])
def test_changed_answer_constraints_never_reuse_a_related_answer(first, second):
    assert not compare_questions(first, second).same
    assert not compare_questions(second, first).same


def test_rewording_with_unchanged_numbers_preserves_a_cache_hit():
    assert compare_questions('What is the probability of rolling a 2 on a standard die?',
                             'What is the probability on a standard die of rolling a 2?').same
