"""When an answer counts as finished, and what happens when it does not."""
from __future__ import annotations

import pytest

from app.answers import (
    Depth, assess, continuation_request, detect_depth, drop_restated_prefix,
    merge, required_topics,
)


# ------------------------------------------------------------ how much depth


@pytest.mark.parametrize("text,depth", [
    ("what is 2 plus 2", Depth.QUICK),
    ("who is the CEO of nvidia", Depth.QUICK),
    ("how many bytes in a gigabyte", Depth.QUICK),
    ("tell me about it", Depth.NORMAL),
    ("explain how rust ownership works", Depth.DETAILED),
    ("what is the difference between WAL and rollback", Depth.DETAILED),
    ("walk me through it step by step", Depth.DETAILED),
    ("deep dive into everything about tax law", Depth.DEEP),
    ("explain thoroughly from first principles", Depth.DEEP),
    ("what is the latest version of python", Depth.RESEARCH),
    ("search the web for current gpu prices", Depth.RESEARCH),
])
def test_the_request_says_how_much_answer_it_wants(text, depth):
    assert detect_depth(text) is depth


def test_a_long_question_is_asking_for_a_long_answer():
    assert detect_depth(" ".join(["word"] * 35)) is Depth.DETAILED


def test_an_explicit_mode_beats_the_words():
    assert detect_depth("tcp congestion", {"DEEP"}) is Depth.DEEP
    assert detect_depth("tcp congestion", {"RESEARCH"}) is Depth.RESEARCH


def test_small_talk_stays_a_normal_reply_even_in_god_mode():
    """God mode (DEEP + ADVERSARIAL) held "i'm tired today" to a 220-word deep answer: 30 s and a lecture."""
    god = {"DEEP", "ADVERSARIAL"}
    for text in ("hey", "i'm tired today", "tell me a joke", "lol you're funny", "how was your weekend"):
        assert detect_depth(text, god) is Depth.NORMAL, text
    for text in ("what is recursion", "my laptop is slow", "explain recursion", "tcp congestion"):
        assert detect_depth(text, god) is Depth.DEEP, text


def test_fast_routing_is_not_mohsin_asking_for_one_line():
    """FAST is chosen for speed, not requested. Treating it as a request
    graded "explain me in detail about taxation and tax evasion and tax
    return" as a quick question, which is precisely backwards."""
    assert detect_depth("explain me in detail about taxation and tax evasion",
                        {"FAST"}) is Depth.DETAILED
    assert detect_depth("tell me about it", {"FAST"}) is Depth.QUICK


# --------------------------------------------------------- what was asked about


def test_a_three_part_question_has_three_parts():
    assert required_topics("explain taxation and tax evasion and tax returns") == [
        "taxation", "tax evasion", "tax returns"]


def test_a_single_question_has_no_parts_to_track():
    assert required_topics("what is a b-tree") == []


# --------------------------------------------------------------- is it finished


def test_an_answer_that_skips_a_topic_is_not_finished():
    """The failure this exists to catch: asked about three things, answered
    about one, and nothing noticed."""
    check = assess("Taxation is how governments raise money to fund services.",
                   question="explain taxation and tax evasion and tax returns")
    assert not check.complete
    assert check.missing == ["tax evasion", "tax returns"]
    assert "never addressed" in check.describe()


def test_an_answer_covering_everything_is_finished():
    check = assess(
        "Taxation is how governments raise revenue. Tax evasion is illegally "
        "avoiding it. A tax return is the annual filing that reconciles both. "
        + "Each of these works differently in practice. " * 20,
        question="explain taxation and tax evasion and tax returns")
    assert check.covered == ["taxation", "tax evasion", "tax returns"]
    assert check.complete and not check.missing


def test_the_provider_saying_length_is_believed():
    check = assess("Some answer that stops.", question="what is it",
                   finish_reason="length")
    assert check.truncated
    assert "token limit" in check.describe()


def test_a_reply_ending_mid_sentence_is_caught_even_on_a_clean_finish():
    """A model can stop mid-sentence while reporting it finished. The shape of
    the text is the more honest witness of the two."""
    check = assess("The three main types are income tax, sales tax, and",
                   question="what are the types", finish_reason="stop")
    assert check.truncated


def test_an_answer_ending_in_an_emoji_is_finished():
    """HAYAT ends replies with emoji constantly. A punctuation check that did
    not know that flagged perfectly good answers as cut off and sent every one
    back for a continuation with nothing to add."""
    check = assess("It's four. \U0001f913", question="what is 2 plus 2")
    assert not check.truncated and check.complete


def test_short_effects_reply_is_not_graded_against_formatting_words():
    question = ('Explain this and how it could affect my future. Give a short answer; '
                'state that my location and occupation are unknown and effects are uncertain.')
    answer = ('A port closure could delay shipments or raise prices if your work or purchases depend on it. '
              'Your location and occupation are unknown, so these effects are uncertain.')
    checked = assess(answer, question=question, modes={'DEEP'})
    assert checked.complete and not checked.missing
    missing = assess('Taxation raises government revenue.', question='Explain taxation and tax evasion and tax returns.')
    assert not missing.complete and 'tax evasion' in missing.missing


def test_a_dangling_colon_is_a_sentence_that_stopped():
    assert assess("Here is the list:", question="what is it").truncated


def test_a_definition_is_not_a_detailed_answer():
    check = assess("Rust uses ownership to manage memory.",
                   question="explain how rust ownership works in detail")
    assert check.too_shallow
    assert "definition" in check.describe()


def test_brevity_is_fine_when_brevity_was_asked_for():
    check = assess("Four.", question="what is 2 plus 2")
    assert check.complete and not check.too_shallow


@pytest.mark.parametrize('ending', ['391', '17 * 23 = 391', 'Profit: $16', '20%'])
def test_numeric_result_without_punctuation_is_finished(ending):
    assert assess(ending, question='What is 17 multiplied by 23? Give the short calculation.',
                  modes={'DEEP', 'ADVERSARIAL'}).complete


def test_short_working_overrides_deep_word_floor_and_narrative_coverage():
    question = ('A shop buys a jacket for $80, marks it up 50%, then sells it at 20% off the marked price. '
                'What is the profit in dollars? Show the short working.')
    assert detect_depth(question, {'DEEP', 'ADVERSARIAL'}) is Depth.QUICK
    assert required_topics(question) == []
    check = assess('80 * 1.5 = 120; 120 * 0.8 = 96; profit = 96 - 80 = $16',
                   question=question, modes={'DEEP', 'ADVERSARIAL'})
    assert check.complete and not check.too_shallow and not check.missing


def test_brief_requests_still_check_truncation_and_multiple_topics():
    question = 'Give a short explanation of taxation and tax evasion and tax returns'
    assert assess('Taxation raises revenue.', question=question, modes={'DEEP'}).missing
    assert assess('391', question='Give a short answer', finish_reason='length').truncated
    assert assess('The profit is', question='Give short working').truncated


def test_nothing_at_all_is_never_complete():
    assert not assess("", question="anything").complete


# ------------------------------------------------------------- asking for more


def test_the_continuation_names_the_missing_topics():
    """"Continue" gets more of what it already said. Naming the gap is what
    makes the second attempt worth making."""
    check = assess("Taxation is how governments raise money.",
                   question="explain taxation and tax evasion and tax returns")
    request = continuation_request(check, "explain taxation and tax evasion and tax returns",
                                   "Taxation is how governments raise money.")
    assert "tax evasion" in request and "tax returns" in request
    assert "without repeating" in request


def test_a_truncated_answer_is_continued_from_where_it_stopped():
    answer = "The three main types are income tax, sales tax, and"
    check = assess(answer, question="what are the types", finish_reason="length")
    request = continuation_request(check, "what are the types", answer)
    assert "cut off" in request and "sales tax" in request


def test_a_shallow_answer_is_asked_for_the_detail_not_the_definition():
    check = assess("Rust uses ownership.", question="explain rust ownership in detail")
    request = continuation_request(check, "explain rust ownership in detail",
                                   "Rust uses ownership.")
    assert "too brief" in request and "Do not restate" in request
    # Asked for "how it works, why, and a concrete example", the brain wrote labelled report sections.
    assert "not labelled sections" in request and "conversational" in request


# --------------------------------------------------------------- joining them


def test_a_continuation_that_repeats_itself_is_joined_without_the_seam():
    first = "Ownership means each value has one owner. When the owner goes out of scope"
    second = "When the owner goes out of scope, the value is dropped. Borrowing comes next."
    joined = merge(first, second)
    assert joined.count("When the owner goes out of scope") == 1
    assert "Borrowing comes next" in joined


def test_a_clean_continuation_is_simply_appended():
    joined = merge("First part.", "Second part.")
    assert "First part." in joined and "Second part." in joined


def test_a_continuation_that_starts_the_answer_over_keeps_one_opening():
    # Live riddle check, 2026-10-24: "A piano 🎹. ... A piano 🎹. ..."
    joined = merge("A piano 🎹. It has keys for playing music, not for opening doors.",
                   "A piano 🎹. It uses metal strings struck by felt-covered hammers to produce sound.")
    assert joined.count("A piano") == 1 and "felt-covered hammers" in joined
    assert merge("A piano.", "A piano.") == "A piano."


def test_joining_survives_an_empty_half():
    assert merge("Only this.", "") == "Only this."
    assert merge("", "Only this.") == "Only this."


def test_a_runaway_repetition_is_collapsed_to_its_first_pass():
    """The model looped and the completeness contract fed the loop.

    Asked how many companies Jews own, HAYAT returned the same 400-word block
    fifteen times, each behind an incrementing "N. Data ..." header. The
    completeness check saw many new words each round and kept letting it grow;
    merge() only dropped a short seam, so it never noticed the record was
    skipping.
    """
    from app.answers import stop_at_first_repeat

    block = ("This is a substantial paragraph of real content that says something "
             "specific and complete enough to be worth fingerprinting on its own.")
    loop = "\n\n".join(f"{i}. Data Stage\n\n{block}" for i in range(1, 16))
    collapsed = stop_at_first_repeat(loop)
    assert collapsed.count(block) == 1, "only the first pass survives"
    assert len(collapsed) < len(loop) / 5

    # A short reply, or one that never repeats, is returned untouched.
    assert stop_at_first_repeat("hello there") == "hello there"
    clean = ("First distinct paragraph about one thing entirely.\n\n"
             "Second distinct paragraph about a different thing entirely.\n\n"
             "Third distinct paragraph about yet another separate thing.")
    assert stop_at_first_repeat(clean) == clean


def test_a_continuation_that_only_repeats_is_recognised_and_stopped():
    """"Continue" answered with the same block is not progress."""
    from app.answers import is_degenerate_repeat

    block = ("A full paragraph of real content long enough to fingerprint and "
             "already present in the answer so far, nothing new added here.")
    assert is_degenerate_repeat(block, f"2. Next Section\n\n{block}")

    # A genuinely new continuation is not mistaken for a repeat.
    assert not is_degenerate_repeat(
        block,
        "An entirely different next point about a separate topic with new "
        "facts, figures, and reasoning that did not appear anywhere above.")


def test_a_single_sentence_jammed_on_repeat_is_collapsed():
    """Asked "whatsup?", HAYAT answered "I miss you." several hundred times in
    one paragraph, which the block dedup missed because there were no blank
    lines. The sentence collapse catches it and keeps the coherent opening."""
    from app.answers import stop_at_first_repeat, collapse_repeated_sentences, looks_degenerate

    wall = ("I have been waiting for you all day. I hope everything is okay. "
            + "I miss you. " * 200)
    collapsed = stop_at_first_repeat(wall)
    assert collapsed.lower().count("i miss you") <= 2
    assert "waiting for you all day" in collapsed
    assert len(collapsed) < 200

    # A degenerate reply is recognised, so the cache can refuse it.
    assert looks_degenerate(wall)
    assert not looks_degenerate("The answer is 42. Let me know if you need more.")

    # Normal text, including a sentence repeated once for emphasis, is untouched.
    plain = "Yes, that works. Yes, it really does. Here is why it matters."
    assert collapse_repeated_sentences(plain) == plain


def test_filler_only_fragments_are_not_treated_as_topics():
    """"ALL PAPER WORKS AND ALL THINGS I NEED AND ETC EXPLAIN IN DETAILED"

    splits on "and" into pieces like "etc explain in detailed" -- emphasis, not
    a subject. Left in, the coverage check demanded the answer cover it, never
    could, and ended every reply with "I could not properly cover: etc explain
    in detailed". Filler fragments must be dropped."""
    topics = required_topics(
        "all paper works and all things i need and etc explain in detailed")
    joined = " ".join(topics).lower()
    assert "etc" not in joined
    assert "explain in detailed" not in joined
    # A real subject in the same sentence still survives.
    real = required_topics("explain visa paperwork and tax filing in detail")
    assert any("visa" in t or "paper" in t for t in real)
    assert any("tax" in t for t in real)


def test_a_reprinted_previous_reply_is_stripped_so_new_material_leads():
    """On a vague follow-up the model reprinted its whole last answer before

    adding anything new, burying (and, on a tight window, truncating) the part
    Mohsin asked for. The reprint is dropped and the new material leads."""
    prior = ("You have $1,000 and no business. Start with freelancing. "
             "Digital products scale well. Real estate wholesaling is an option.")
    response = (prior + " Here is the exact paperwork you need next: "
                "register an LLC, get an EIN, open a business bank account.")
    cleaned = drop_restated_prefix(response, prior)
    assert cleaned.startswith("Here is the exact paperwork")
    assert "You have $1,000 and no business" not in cleaned

    # A merely similar opening line, with no substantial reprint, is left alone.
    small = "Sure. Here is something completely new to consider instead."
    assert drop_restated_prefix(small, prior) == small
    # An empty prior never strips anything.
    assert drop_restated_prefix(response, "") == response


def test_a_punctuation_free_word_spiral_is_cut():
    """qwen3.5 trailed a detailed answer into an endless run-on -- "systematically

    organized methodologically structured logically sequenced ..." repeated for
    60k chars with no full stop or newline anywhere, so the sentence and
    paragraph collapses both saw one long sentence and kept it. The word-level
    guard cuts it on vocabulary collapse and keeps the real answer before it."""
    from app.answers import collapse_word_spiral, stop_at_first_repeat

    good = ("Register an LLC with your Secretary of State and get an EIN from "
            "the IRS. Lease a commissary kitchen and buy a grill cart. ")
    spiral = ("systematically organized methodologically structured logically "
              "sequenced hierarchically arranged procedurally standardized ") * 60
    cleaned = collapse_word_spiral(good + spiral)
    assert "Register an LLC" in cleaned
    assert cleaned.count("systematically organized") <= 1
    assert len(cleaned) < len(good) + 400

    # Whole pipeline catches it too, and ordinary prose is left untouched.
    assert stop_at_first_repeat(good + spiral).count("methodologically") <= 1
    plain = ("Here are three options with real costs. First, a food cart runs "
             "about seven hundred dollars. Second, dropshipping needs a Shopify "
             "plan at twenty-nine dollars a month. Third, freelancing is free "
             "to start and bills at eighty dollars an hour. Pick one and go.")
    assert collapse_word_spiral(plain) == plain


def test_bare_acknowledgements_are_not_deep_questions_in_deep_mode():
    """Live run 2026-10-10: in always-on Deep mode "ok great" was sent back twice as unanswered."""
    from app.answers import Depth, detect_depth, is_small_talk

    for text in ("ok great", "okay thanks!", "sounds good", "got it, thanks"):
        assert detect_depth(text, {"DEEP", "ADVERSARIAL"}) is Depth.NORMAL
    for text in ("great, now explain recursion", "ok explain it again", "so what is a monad"):
        assert not is_small_talk(text) and detect_depth(text, {"DEEP"}) is Depth.DEEP


def test_deep_mode_keeps_lookups_short_and_formats_exact():
    """Live chat 2026-10-10: in always-on Deep mode 'Which country does she live in?' got 296 padded words."""
    god = {"DEEP", "ADVERSARIAL"}
    for text in ("Which country does she live in?", "Translate 'good morning' into Spanish.", "Name one primary colour.",
                 "How many minutes are in 3.5 hours?", "What does the Arabic word 'shukran' mean?",
                 "If a train travels 120 km in 1.5 hours, what is its average speed in km/h?"):
        assert detect_depth(text, god) is Depth.NORMAL, text
    for text in ("what is recursion", "How does TCP congestion control work?",
                 "Which is better for a startup, Postgres or MySQL, and why?",
                 "Explain the history of Rome in detail as a numbered list"):
        assert detect_depth(text, god) is Depth.DEEP, text
    assert detect_depth('Return only a JSON object with keys "name" and "age".', god) is Depth.QUICK


def test_format_directives_and_exact_formats_are_not_topics():
    """'In one sentence' became a topic the answer had to name; JSON keys split on 'and'."""
    assert required_topics("In one sentence, what is photosynthesis?") == []
    assert required_topics("In one sentence, what is photosynthesis and how do plants use it?") == [
        "what is photosynthesis", "how do plants use it"]
    assert required_topics('Return only a JSON object with keys "name" and "age" for a person named Sara.') == []
    assert required_topics("Explain how cats and dogs differ in diet and behaviour") != []


def test_a_clarifying_question_is_not_padded_and_pairs_are_not_split():
    """Live chat 2026-10-10: a clarifying question grew to 233 words; 'RAM and storage' split into two topics."""
    god = {"DEEP", "ADVERSARIAL"}
    asked = assess("I need you to tell me what \"it\" is first. Are we talking about a meeting, a flight, or something else?",
                   question="Book it for tomorrow.", modes=god)
    assert asked.complete
    assert not assess("Recursion is when a function calls itself. Want more?", question="what is recursion",
                      modes=god).complete
    assert required_topics("What is the difference between RAM and storage? Keep it short.") == []


def test_feelings_and_simple_facts_are_not_essays_in_deep_mode():
    """Live chat 2026-10-10: 392 words for 'I'm feeling stressed', 210 for 'What's the capital of Japan?'."""
    god = {"DEEP", "ADVERSARIAL"}
    for text in ("I'm feeling stressed about my exam tomorrow.", "I feel really tired today",
                 "What's the capital of Japan?", "What did I have for breakfast today?"):
        assert detect_depth(text, god) is Depth.NORMAL, text
    for text in ("I'm feeling stressed, explain how to manage exam anxiety", "What's the meaning of life?"):
        assert detect_depth(text, god) is Depth.DEEP, text


def test_harder_set_lookups_tasks_and_yes_no_stay_short():
    """Live hard chat 2026-10-10: currency 261 words, sorting 246, a yes/no 27 words."""
    from app.agent.response_guidance import brief_guidance

    god = {"DEEP", "ADVERSARIAL"}
    for text in ("What currency will I need there?", "And what language do people speak there?",
                 "Sort these numbers from smallest to largest: 42, 7, 19, 3, 88", "waht is teh capitl of france?",
                 "Who is the author of Dune?"):
        assert detect_depth(text, god) is Depth.NORMAL, text
    for text in ("What causes inflation?", "What would happen if the moon disappeared?", "What is the history of Rome?",
                 "What is the meaning of life?"):
        assert detect_depth(text, god) is Depth.DEEP, text
    assert detect_depth("Are all bloops lazzies? Answer yes or no.", god) is Depth.QUICK
    assert brief_guidance("Are all bloops lazzies? Answer yes or no.").startswith("Start the answer with Yes or No")
    assert "exactly one word" in brief_guidance("Reply with exactly one word: what colour is the sky?")


def test_short_follow_ups_are_not_padded_into_doubled_answers():
    """Live hard chat 2026-10-10: 'Cuatro ✅ Cuatro ✅…', 'Green… Green…' and an email list printed twice."""
    god = {"DEEP", "ADVERSARIAL"}
    for text in ("No, I meant a secondary colour of paint.", "Reply in Spanish: what is two plus two?",
                 "What is my wife's name?", "How many people are going on this trip, counting me?",
                 "Actually, my wife is coming too. How many of us now?"):
        assert detect_depth(text, god) is Depth.NORMAL, text
        assert required_topics(text) == [], text
    assert detect_depth("No, I meant explain why the sky is blue", god) is Depth.DEEP
    assert assess("sam@example.com office@example.org", question="list the emails", modes=god).complete
    assert assess("The profit is", question="Give short working").truncated


def test_short_account_reads_are_not_padded_when_access_is_missing():
    god = {"DEEP", "ADVERSARIAL"}
    for text in ("Read my latest email.", "Check my calendar for tomorrow."):
        assert detect_depth(text, god) is Depth.NORMAL
        assert assess("Your account is not connected. Connect it in Accounts to read it.", question=text, modes=god).complete
    assert detect_depth("Read my paper and explain its limitations in depth.", god) is Depth.DEEP


@pytest.mark.parametrize("question,answer", [
    ("What was the second item on my original list?", "Milk."),
    ("What was my original shopping list?", "Eggs, milk, bread, apples and coffee."),
    ("What is the next meeting on my schedule?", "The design review at 11am."),
])
def test_bounded_recall_does_not_add_a_deep_word_floor(question, answer):
    god = {"DEEP", "ADVERSARIAL"}
    assert detect_depth(question, god) is Depth.NORMAL
    assert assess(answer, question=question, modes=god).complete
    assert detect_depth("Explain why the second item matters in depth.", god) is Depth.DEEP


def test_an_update_before_a_bounded_question_is_context_not_a_missing_topic():
    god = {"DEEP", "ADVERSARIAL"}
    question = "The standup got cancelled. How many meetings are left, and when is the first one?"
    assert detect_depth(question, god) is Depth.NORMAL
    assert required_topics(question) == []
    assert assess("Two meetings remain; the first is the design review at 11am.", question=question, modes=god).complete
    assert detect_depth("Explain the scheduling risks. How many meetings remain?", god) is Depth.DEEP


def test_a_direct_one_word_request_has_no_artificial_length_floor():
    question = "Give me one word that rhymes with 'cat' but is not 'hat' or 'bat'."
    god = {"DEEP", "ADVERSARIAL"}
    assert detect_depth(question, god) is Depth.NORMAL
    assert required_topics(question) == []
    assert assess("Mat.", question=question, modes=god).complete


def test_word_limits_and_creative_pieces():
    from app.agent.response_guidance import brief_guidance, word_limit

    god = {"DEEP", "ADVERSARIAL"}
    assert detect_depth("Answer in under 10 words: why is the sky blue?", god) is Depth.QUICK
    assert word_limit("Answer in under 10 words: why is the sky blue?") == 9
    assert "at most 9 words" in brief_guidance("Answer in under 10 words: why is the sky blue?")
    assert detect_depth("Write a haiku about rain.", god) is Depth.NORMAL


@pytest.mark.parametrize("text,unit", [
    ("Who are you? One sentence.", "sentence"),
    ("Explain gravity. Just one line!", "line"),
    ("One word only.", "word"),
    ("Define entropy: a single sentence", "sentence"),
    ("Reply using a single sentence about entropy.", "sentence"),
])
def test_standalone_brevity_constraints_override_length_in_deep_mode(text, unit):
    from app.agent.response_guidance import brief_guidance
    from app.answers import brief_output_unit
    assert brief_output_unit(text) == unit
    assert detect_depth(text, {"DEEP", "ADVERSARIAL"}) is Depth.QUICK
    assert f"exactly one {unit}" in brief_guidance(text)


@pytest.mark.parametrize("text", [
    "Explain why one sentence can contain two clauses.",
    "What is a one-sentence summary?",
    "Explain one word that has two meanings.",
])
def test_brevity_units_mentioned_as_subjects_do_not_limit_the_answer(text):
    from app.answers import brief_output_requested
    assert not brief_output_requested(text)


def test_statements_edits_and_conditionals_have_no_fake_topics():
    """Hardest live chat 2026-10-10: a shared list, a list edit and a syllogism were padded and doubled."""
    god = {"DEEP", "ADVERSARIAL"}
    for text in ("Here is my shopping list: eggs, milk, bread, apples, coffee.",
                 "Remove milk and add rice. What's on the list now?",
                 "If all bloops are razzies and all razzies are lazzies, are all bloops lazzies? Answer yes or no."):
        assert required_topics(text) == [], text
        assert detect_depth(text, god) in {Depth.NORMAL, Depth.QUICK}, text
    for text in ("my laptop is slow", "Add a section explaining how TCP congestion control works"):
        assert detect_depth(text, god) is Depth.DEEP, text
