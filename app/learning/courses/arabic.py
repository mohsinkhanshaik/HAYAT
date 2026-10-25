"""A beginner Arabic course: the alphabet, the vowel marks, and everyday words.

Written out here rather than generated. A model asked for Arabic on the fly
invents plausible spellings and meanings, and a beginner has no way to tell;
a fixed course can be checked once and then trusted. Every item carries the
fully vowelled spelling (what a learner reads and what the voice speaks),
a transliteration, and the English meanings accepted as answers.

Modern Standard Arabic throughout, so it is understood in every Arabic-speaking
country. Where a phrase changes for a man or a woman, the masculine form is
used and the note says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    item_id: str
    kind: str                     # letter | mark | word | phrase
    arabic: str                   # fully vowelled; spoken and displayed
    transliteration: tuple[str, ...]
    meanings: tuple[str, ...]     # accepted English answers; first is shown
    note: str = ""
    spoken: str = ""              # what the voice says, when it differs (a letter's name)
    joins: bool = True            # letters: whether it joins the letter after it


@dataclass(frozen=True)
class Unit:
    unit_id: str
    title: str
    intro: str
    items: tuple[Item, ...] = field(default_factory=tuple)


def _letter(arabic, name, spoken, sounds, note, joins=True):
    return Item(f"letter-{name}", "letter", arabic, tuple(sounds), (name,), note, spoken, joins)


def _word(item_id, arabic, transliteration, meanings, note="", kind="word"):
    translits = (transliteration,) if isinstance(transliteration, str) else tuple(transliteration)
    return Item(item_id, kind, arabic, translits, tuple(meanings), note)


def _phrase(item_id, arabic, transliteration, meanings, note=""):
    return _word(item_id, arabic, transliteration, meanings, note, kind="phrase")


LETTERS = (
    _letter("ا", "alif", "أَلِف", ("a", "aa", "alif"), "A long 'aa'. It never joins the letter after it.", joins=False),
    _letter("ب", "baa", "بَاء", ("b", "ba", "baa"), "Like English 'b'. One dot below."),
    _letter("ت", "taa", "تَاء", ("t", "ta", "taa"), "Like English 't'. Two dots above."),
    _letter("ث", "thaa", "ثَاء", ("th", "tha", "thaa"), "'th' as in 'think'. Three dots above."),
    _letter("ج", "jiim", "جِيم", ("j", "ja", "jiim", "jeem", "jim"), "Like English 'j'. One dot inside."),
    _letter("ح", "haa (throat)", "حَاء", ("h", "ha", "haa", "7"), "A breathy 'h' from deep in the throat, as if fogging a mirror."),
    _letter("خ", "khaa", "خَاء", ("kh", "kha", "khaa", "5"), "Like the 'ch' in Scottish 'loch'. One dot above."),
    _letter("د", "daal", "دَال", ("d", "da", "daal", "dal"), "Like English 'd'. Does not join the next letter.", joins=False),
    _letter("ذ", "dhaal", "ذَال", ("dh", "dha", "dhaal", "thal"), "'th' as in 'this'. Does not join the next letter.", joins=False),
    _letter("ر", "raa", "رَاء", ("r", "ra", "raa"), "A rolled 'r'. Does not join the next letter.", joins=False),
    _letter("ز", "zaay", "زَاي", ("z", "za", "zaay", "zay", "zain"), "Like English 'z'. Does not join the next letter.", joins=False),
    _letter("س", "siin", "سِين", ("s", "sa", "siin", "seen", "sin"), "Like English 's'."),
    _letter("ش", "shiin", "شِين", ("sh", "sha", "shiin", "sheen", "shin"), "Like English 'sh'. Three dots above."),
    _letter("ص", "saad (heavy)", "صَاد", ("s", "sa", "saad", "sad"), "A heavy 's', said with the tongue low and the mouth full."),
    _letter("ض", "daad (heavy)", "ضَاد", ("d", "da", "daad", "dad"), "A heavy 'd'. Arabic is called 'the language of the daad' after it."),
    _letter("ط", "taa (heavy)", "طَاء", ("t", "ta", "taa", "6"), "A heavy 't'."),
    _letter("ظ", "dhaa (heavy)", "ظَاء", ("dh", "dha", "dhaa", "za", "zaa"), "A heavy 'th' as in 'this'."),
    _letter("ع", "ayn", "عَيْن", ("ayn", "ain", "3", "'a", "a"), "A squeeze in the throat with no English match. Say 'a' while tightening the throat."),
    _letter("غ", "ghayn", "غَيْن", ("gh", "gha", "ghayn", "ghain"), "Like a French 'r', gargled softly."),
    _letter("ف", "faa", "فَاء", ("f", "fa", "faa"), "Like English 'f'. One dot above."),
    _letter("ق", "qaaf", "قَاف", ("q", "qa", "qaaf", "qaf"), "A 'k' made far back in the throat. Two dots above."),
    _letter("ك", "kaaf", "كَاف", ("k", "ka", "kaaf", "kaf"), "Like English 'k'."),
    _letter("ل", "laam", "لَام", ("l", "la", "laam", "lam"), "Like English 'l'."),
    _letter("م", "miim", "مِيم", ("m", "ma", "miim", "meem", "mim"), "Like English 'm'."),
    _letter("ن", "nuun", "نُون", ("n", "na", "nuun", "noon", "nun"), "Like English 'n'. One dot above."),
    _letter("ه", "haa (soft)", "هَاء", ("h", "ha", "haa"), "A light 'h' as in 'hat'."),
    _letter("و", "waaw", "وَاو", ("w", "wa", "waaw", "waw", "u", "uu", "oo"), "'w', or a long 'uu'. Does not join the next letter.", joins=False),
    _letter("ي", "yaa", "يَاء", ("y", "ya", "yaa", "i", "ii", "ee"), "'y', or a long 'ii'. Two dots below."),
)

_BY_LETTER = {item.arabic: item for item in LETTERS}


def _letters(*chars: str) -> tuple[Item, ...]:
    return tuple(_BY_LETTER[char] for char in chars)


MARKS = (
    _word("mark-fatha", "بَ", ("ba",), ("fatha", "a"), "Fatha: a small stroke above. Adds a short 'a'.", "mark"),
    _word("mark-kasra", "بِ", ("bi",), ("kasra", "i"), "Kasra: a small stroke below. Adds a short 'i'.", "mark"),
    _word("mark-damma", "بُ", ("bu",), ("damma", "u"), "Damma: a small curl above. Adds a short 'u'.", "mark"),
    _word("mark-sukun", "بْ", ("b",), ("sukun", "no vowel"), "Sukun: a small circle above. The letter has no vowel.", "mark"),
    _word("mark-shadda", "بَّ", ("bba",), ("shadda", "double"), "Shadda: a small 'w' shape. The letter is said twice as long.", "mark"),
    _word("mark-tanwin", "بًا", ("ban",), ("tanwin", "an"), "Tanwin: a doubled mark at a word's end adds 'n' (here 'an').", "mark"),
    _word("mark-long-a", "بَا", ("baa",), ("long a", "baa"), "Fatha followed by alif makes a long 'aa'.", "mark"),
    _word("mark-long-i", "بِي", ("bii", "bee"), ("long i", "bii"), "Kasra followed by yaa makes a long 'ii'.", "mark"),
    _word("mark-long-u", "بُو", ("buu", "boo"), ("long u", "buu"), "Damma followed by waaw makes a long 'uu'.", "mark"),
)

GREETINGS = (
    _phrase("hello", "مَرْحَبًا", ("marhaban", "marhaba"), ("hello", "hi")),
    _phrase("salam", "السَّلَامُ عَلَيْكُمْ", ("as-salamu alaykum", "assalamu alaikum", "salam alaykum"),
            ("peace be upon you", "hello"), "The everyday greeting across the Arab world."),
    _phrase("salam-reply", "وَعَلَيْكُمُ السَّلَامُ", ("wa alaykumu s-salam", "wa alaikum assalam", "walaikum salam"),
            ("and upon you peace", "and peace be upon you"), "The reply to as-salamu alaykum."),
    _phrase("good-morning", "صَبَاحُ الْخَيْرِ", ("sabahu l-khayr", "sabah al khair", "sabah el kheir"), ("good morning",)),
    _phrase("good-morning-reply", "صَبَاحُ النُّورِ", ("sabahu n-nur", "sabah an nur", "sabah el nour"),
            ("good morning", "morning of light"), "The reply to sabah al-khayr: 'morning of light'."),
    _phrase("good-evening", "مَسَاءُ الْخَيْرِ", ("masa'u l-khayr", "masa al khair"), ("good evening",)),
    _phrase("how-are-you", "كَيْفَ حَالُكَ؟", ("kayfa haluka", "kayf halak", "kaifa haluk"),
            ("how are you",), "Said to a man. To a woman: كَيْفَ حَالُكِ (kayfa haluki)."),
    _phrase("fine", "أَنَا بِخَيْرٍ", ("ana bikhayr", "ana bi khair"), ("i am fine", "i'm fine", "i am well", "fine")),
    _phrase("welcome", "أَهْلًا وَسَهْلًا", ("ahlan wa sahlan", "ahlan wasahlan"), ("welcome",)),
    _phrase("goodbye", "مَعَ السَّلَامَةِ", ("ma'a s-salama", "maa salama", "ma as salama"), ("goodbye", "bye")),
)

COURTESY = (
    _word("thanks", "شُكْرًا", ("shukran",), ("thank you", "thanks")),
    _phrase("thanks-a-lot", "شُكْرًا جَزِيلًا", ("shukran jazilan", "shukran jazeelan"), ("thank you very much", "thanks a lot")),
    _word("afwan", "عَفْوًا", ("afwan", "'afwan"), ("you're welcome", "you are welcome", "excuse me")),
    _phrase("please", "مِنْ فَضْلِكَ", ("min fadlika", "min fadlak"), ("please",), "Said to a man. To a woman: مِنْ فَضْلِكِ (min fadliki)."),
    _word("yes", "نَعَمْ", ("na'am", "naam"), ("yes",)),
    _word("no", "لَا", ("la", "laa"), ("no",)),
    _word("sorry", "آسِفٌ", ("asif", "aasif"), ("sorry", "i'm sorry"), "Said by a man. A woman says آسِفَةٌ (asifa)."),
    _phrase("inshallah", "إِنْ شَاءَ اللَّهُ", ("in sha allah", "inshallah", "insha allah"), ("god willing", "if god wills")),
)

NUMBERS = (
    _word("n0", "صِفْرٌ", ("sifr",), ("zero", "0")),
    _word("n1", "وَاحِدٌ", ("wahid", "waahid"), ("one", "1")),
    _word("n2", "اِثْنَانِ", ("ithnan", "ithnaan", "itnan"), ("two", "2")),
    _word("n3", "ثَلَاثَةٌ", ("thalatha", "talata"), ("three", "3")),
    _word("n4", "أَرْبَعَةٌ", ("arba'a", "arbaa"), ("four", "4")),
    _word("n5", "خَمْسَةٌ", ("khamsa",), ("five", "5")),
    _word("n6", "سِتَّةٌ", ("sitta",), ("six", "6")),
    _word("n7", "سَبْعَةٌ", ("sab'a", "saba"), ("seven", "7")),
    _word("n8", "ثَمَانِيَةٌ", ("thamaniya", "thamanya"), ("eight", "8")),
    _word("n9", "تِسْعَةٌ", ("tis'a", "tisa"), ("nine", "9")),
    _word("n10", "عَشَرَةٌ", ("ashara", "'ashara"), ("ten", "10")),
)

PRONOUNS = (
    _word("i", "أَنَا", ("ana",), ("i", "me")),
    _word("you-m", "أَنْتَ", ("anta",), ("you", "you (man)"), "To a man."),
    _word("you-f", "أَنْتِ", ("anti",), ("you", "you (woman)"), "To a woman."),
    _word("he", "هُوَ", ("huwa",), ("he", "it")),
    _word("she", "هِيَ", ("hiya",), ("she", "it")),
    _word("we", "نَحْنُ", ("nahnu",), ("we",)),
    _word("they", "هُمْ", ("hum",), ("they",)),
)

FAMILY = (
    _word("father", "أَبٌ", ("ab",), ("father", "dad")),
    _word("mother", "أُمٌّ", ("umm",), ("mother", "mom", "mum")),
    _word("brother", "أَخٌ", ("akh",), ("brother",)),
    _word("sister", "أُخْتٌ", ("ukht",), ("sister",)),
    _word("son", "اِبْنٌ", ("ibn",), ("son",)),
    _word("daughter", "بِنْتٌ", ("bint",), ("daughter", "girl")),
    _word("family", "عَائِلَةٌ", ("a'ila", "aila"), ("family",)),
)

EVERYDAY = (
    _word("water", "مَاءٌ", ("ma'", "maa"), ("water",)),
    _word("bread", "خُبْزٌ", ("khubz",), ("bread",)),
    _word("house", "بَيْتٌ", ("bayt", "beit"), ("house", "home")),
    _word("book", "كِتَابٌ", ("kitab", "kitaab"), ("book",)),
    _word("pen", "قَلَمٌ", ("qalam",), ("pen", "pencil")),
    _word("door", "بَابٌ", ("bab", "baab"), ("door",)),
    _word("car", "سَيَّارَةٌ", ("sayyara", "sayara"), ("car",)),
    _word("school", "مَدْرَسَةٌ", ("madrasa",), ("school",)),
    _word("day", "يَوْمٌ", ("yawm", "yom"), ("day",)),
    _word("night", "لَيْلٌ", ("layl", "leil"), ("night",)),
    _word("sun", "شَمْسٌ", ("shams",), ("sun",)),
    _word("moon", "قَمَرٌ", ("qamar",), ("moon",)),
)

QUESTIONS = (
    _word("what", "مَا", ("ma", "maa"), ("what",)),
    _word("who", "مَنْ", ("man",), ("who",)),
    _word("where", "أَيْنَ", ("ayna", "ayn"), ("where",)),
    _word("when", "مَتَى", ("mata", "mataa"), ("when",)),
    _word("why", "لِمَاذَا", ("limadha", "limaza", "limatha"), ("why",)),
    _word("how", "كَيْفَ", ("kayfa", "kayf"), ("how",)),
    _word("how-many", "كَمْ", ("kam",), ("how many", "how much")),
)

SENTENCES = (
    _phrase("your-name", "مَا اسْمُكَ؟", ("ma smuka", "ma ismuk", "ma ismak"), ("what is your name", "what's your name"),
            "Said to a man. To a woman: مَا اسْمُكِ (ma smuki)."),
    _phrase("my-name", "اِسْمِي", ("ismi",), ("my name", "my name is")),
    _phrase("from-where", "مِنْ أَيْنَ أَنْتَ؟", ("min ayna anta",), ("where are you from",), "Said to a man."),
    _phrase("student", "أَنَا طَالِبٌ", ("ana talib", "ana taalib"), ("i am a student", "i'm a student"), "Said by a man. A woman: أَنَا طَالِبَةٌ."),
    _phrase("speak-little", "أَتَكَلَّمُ الْعَرَبِيَّةَ قَلِيلًا", ("atakallamu l-arabiyyata qalilan",),
            ("i speak arabic a little", "i speak a little arabic")),
    _phrase("dont-understand", "لَا أَفْهَمُ", ("la afham", "la afhamu"), ("i don't understand", "i do not understand")),
    _phrase("speak-english", "هَلْ تَتَكَلَّمُ الْإِنْجِلِيزِيَّةَ؟", ("hal tatakallamu l-injiliziyya",),
            ("do you speak english",), "Said to a man."),
    _phrase("bathroom", "أَيْنَ الْحَمَّامُ؟", ("ayna l-hammam", "ayn al hammam"), ("where is the bathroom", "where is the toilet")),
    _phrase("price", "كَمِ السِّعْرُ؟", ("kami s-si'r", "kam al sir"), ("how much is it", "what is the price", "how much is the price")),
    _phrase("want-water", "أُرِيدُ مَاءً", ("uridu ma'an", "urid maa"), ("i want water", "i would like water")),
)

UNITS = (
    Unit("letters-1", "Letters: ا ب ت ث", "Arabic is written right to left. Start with four letters; the last three share one shape and differ only by their dots.", _letters("ا", "ب", "ت", "ث")),
    Unit("letters-2", "Letters: ج ح خ", "Three letters with one shape: a dot inside, no dot, a dot above.", _letters("ج", "ح", "خ")),
    Unit("letters-3", "Letters: د ذ ر ز", "Four letters that never join the letter after them.", _letters("د", "ذ", "ر", "ز")),
    Unit("marks", "Vowel marks", "Short vowels are small marks above or below a letter. Beginner texts show them; everyday writing usually leaves them out.", MARKS),
    Unit("letters-4", "Letters: س ش ص ض", "Two light letters and their heavy partners.", _letters("س", "ش", "ص", "ض")),
    Unit("greetings", "Greetings", "Phrases you will use every day.", GREETINGS),
    Unit("letters-5", "Letters: ط ظ ع غ", "Two heavy letters, and ع and غ, which come from the throat.", _letters("ط", "ظ", "ع", "غ")),
    Unit("courtesy", "Courtesy", "Please, thank you, yes and no.", COURTESY),
    Unit("letters-6", "Letters: ف ق ك ل", "Four common letters. ق is a deeper sound than ك.", _letters("ف", "ق", "ك", "ل")),
    Unit("numbers", "Numbers 0 to 10", "Arabic numerals are written ٠ ١ ٢ ٣ ٤ ٥ ٦ ٧ ٨ ٩, but here we learn the words.", NUMBERS),
    Unit("letters-7", "Letters: م ن ه و ي", "The last five letters. و and ي also make long vowels.", _letters("م", "ن", "ه", "و", "ي")),
    Unit("pronouns", "I, you, he, she", "Arabic has different words for 'you' to a man and to a woman.", PRONOUNS),
    Unit("family", "Family", "Words for the people closest to you.", FAMILY),
    Unit("everyday", "Everyday words", "Things you see and use every day.", EVERYDAY),
    Unit("questions", "Question words", "What, who, where, when, why, how.", QUESTIONS),
    Unit("sentences", "Useful sentences", "Short sentences for a first conversation.", SENTENCES),
)

COURSE = {
    "course_id": "arabic",
    "title": "Arabic",
    "language_code": "ar",
    "script_direction": "rtl",
    "units": UNITS,
    "letters": LETTERS,
}


def letter_forms(item: Item) -> dict[str, str]:
    """The four positional shapes, drawn with the joining stroke (tatweel).

    A letter that does not join the one after it has no separate initial and
    medial shapes: they look like its isolated and final forms.
    """
    tatweel = "ـ"
    char = item.arabic
    if item.joins:
        return {"isolated": char, "initial": char + tatweel, "medial": tatweel + char + tatweel, "final": tatweel + char}
    return {"isolated": char, "initial": char, "medial": tatweel + char, "final": tatweel + char}


def all_items() -> dict[str, Item]:
    return {item.item_id: item for unit in UNITS for item in unit.items}
