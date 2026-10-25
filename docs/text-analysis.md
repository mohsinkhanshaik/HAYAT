# Text analysis (NLTK)

HAYAT can analyse any text or file on this computer with NLTK, the Natural Language Toolkit. It reports how long the text is, how hard it is to read, what it is mostly about and what tone it takes. Nothing leaves the computer. The code is in [app/text_analysis.py](../app/text_analysis.py).

## Say it in chat

| You say | What happens |
| --- | --- |
| "Sentiment of: I love this but hate the price" | Tone of the text you paste after the colon |
| "Check the tone of this email: …" | The same, for an email or message |
| "How readable is D:/AI/docs/network.md" | Readability of a file (text, Markdown, CSV, log, JSON, HTML, PDF or Word) |
| "Keywords in notes.txt" | Key words and repeated phrases of a file |
| "Analyze this text: …" | Everything below, for pasted text |
| "Set up language tools" | Downloads NLTK's language data once (about 22 MB) |

"Analyze report.pdf" still means "what does it say?" and goes to the document tools. Text analysis runs only when you ask about the text itself: its sentiment, tone, readability, keywords or word count, or "analyze this text".

## What you get

- **Size:** words, sentences, unique words, average sentence length and reading time (at 230 words a minute).
- **Readability:** the Flesch reading-ease score with its meaning (very easy … very difficult) and a school grade level.
- **Key words and repeated phrases:** common words such as "the" and "and" are left out.
- **Sentiment:** NLTK's VADER scores the text as positive, negative or neutral (from −1 to +1). It also quotes the most positive and the most negative sentence.
- **Parts of speech:** the share of nouns, verbs, adjectives and adverbs. Writing heavy in adjectives and adverbs reads as descriptive, and writing heavy in nouns reads as dense.

The language data is a sentence splitter, stop-word lists, the VADER lexicon and a part-of-speech tagger. It lives in `data/nltk_data`, inside the HAYAT folder, so it moves with the folder and works offline. Before it is set up, the analysis still runs with simpler built-in rules. The answer names the parts that are approximate and says "set up language tools" to make them exact.

## NLTK's security fix

NLTK 3.10.3, the newest release, has an open advisory: **PYSEC-2026-3740**. Six of its model save and load functions bypass NLTK's own file sandbox. They read and write paths outside the allowed data folders with Python's plain `open()`. The six are `TransitionParser.train`/`parse`, `AveragedPerceptron.save`/`load`, `PerceptronTagger.save_to_json` and `nltk.classify.maxent.save_maxent_params`. No fixed version exists yet.

HAYAT closes this itself in [app/security/nltk_guard.py](../app/security/nltk_guard.py):

1. Each of the six functions is wrapped so that its path first goes through NLTK's own `pathsec.validate_path`.
2. A path outside the allowed folders raises `PermissionError`, which is exactly how the already-guarded sibling functions behave.
3. Paths inside the allowed folders work as before.

The guard is installed when HAYAT starts, before anything can import NLTK. An import hook then hardens these modules the moment they load. `data/nltk_data` is added to NLTK's allowed folders. The tests check each function on both sides of the boundary, and in a fresh process ([tests/test_text_analysis.py](../tests/test_text_analysis.py)).

`pip-audit` will keep listing the advisory, because it checks version numbers and not code. Once a fixed NLTK is released, upgrade it, and the guard can be removed.
