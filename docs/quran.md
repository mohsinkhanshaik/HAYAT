# Reading the Quran with HAYAT

HAYAT reads the Quran from a published text. The model never recites it from
memory, because a language model reciting scripture drops and alters words, and
someone reading along to learn would not notice.

## How to use it

- "Start quran from the beginning in Roman English", "Read Surah Al-Fatiha", "Open the quran"
- "Read Surah Yasin", "Recite surah 36 ayah 5", "Read quran 2:255"
- "Continue quran". Right after a reading, plain "continue" or "next" works too.

Each reading shows up to seven ayahs. Every ayah gives the Roman-English
transliteration first, then the Arabic, then the meaning in English. HAYAT keeps
your place, so "continue quran" goes on from the last ayah shown, into the next
surah when one ends. Surahs can be named the way people say them (Fatiha,
Al-Fatiha, alfatiha, Yasin, Yaseen, Asr, Al-Mulk) or by number, 1 to 114.

The Bismillah opens every surah except At-Tawba. The Arabic text joins it to
ayah 1, but the transliteration and translation do not, so HAYAT shows it as
its own opening line. Al-Fatiha keeps it as ayah 1. The words are matched by
their letters, not their vowel marks, and a live check covered all 114 surahs.

Questions about the Quran ("what does the quran say about patience") are not
readings, so they are answered as ordinary questions.

## Where the words come from

The Tanzil Uthmani Arabic text, its English transliteration and the Saheeh
International translation come together from the free, keyless
[api.alquran.cloud](https://alquran.cloud/api). After its first read a surah is
cached in `data/knowledge/quran/`, so it reads again without the internet. The
reading position is kept there in `position.json`.

Transliteration is only a reading aid. Some Arabic sounds have no English
letter, so learn pronunciation (tajweed) from a qualified teacher or a recited
recording. For learning Arabic itself, say "Teach me Arabic".

Tools: `quran_read` (surah, start_ayah, count up to 30, arabic) and
`quran_continue`. Both use the `public_api_get` permission, which is already
allowed. Privacy mode switches them off.
