"""Words with more than one meaning, read the way a person reads them: from the words around them.

"How fast is a jaguar" is about the animal; "jaguar price" is about the car. Research used to send both to the
search engines as typed. The results mixed the animal and the car, and the claims checked against the question
mixed them too, because both senses mention "jaguar".

:func:`resolve` picks the meaning:

1. from clue words in the question itself ("speed", "habitat" for the animal; "engine", "dealer" for the car);
2. otherwise from the recent conversation (the owner was talking about cars a minute ago);
3. otherwise the meaning most people intend, and the answer says so, so the owner can correct it in one word.

The chosen meaning's qualifier is added to the search queries only ("jaguar animal speed"). The question and its
answer keep the owner's words. The table is small and written by hand; a word not in it is searched as typed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Meaning:
    name: str
    qualifier: str
    clues: tuple[str, ...]


@dataclass
class Reading:
    word: str
    meaning: Meaning
    chosen_by: str  # "question", "conversation" or "most common"
    others: tuple[str, ...]

    @property
    def note(self) -> str:
        """One line for the answer when the meaning was assumed rather than read from the owner's words."""
        if self.chosen_by == "most common":
            return (f"I read “{self.word}” as {self.meaning.name}, its most common meaning; say if you meant "
                    + " or ".join(self.others) + ".")
        if self.chosen_by == "conversation":
            return f"I read “{self.word}” as {self.meaning.name}, from what we were just talking about."
        return ""


def _m(name: str, qualifier: str, clues: str) -> Meaning:
    return Meaning(name, qualifier, tuple(clues.split()))


#: First meaning listed is the one most people intend when nothing else says.
MEANINGS: dict[str, tuple[Meaning, ...]] = {
    "jaguar": (_m("the animal", "animal", "animal cat wild speed fast run habitat prey rainforest species eat weight "
                  "bite cub cubs endangered zoo"),
               _m("the car maker", "car", "car cars model engine dealer price mpg electric ftype xf xj suv drive "
                  "horsepower")),
    "python": (_m("the programming language", "programming", "code coding install pip version library script error "
                  "function django flask package import syntax tutorial"),
               _m("the snake", "snake", "snake snakes venom venomous bite reptile length species eat burmese pet "
                  "constrictor")),
    "apple": (_m("the company", "company", "iphone mac macbook ios ipad stock shares ceo company watch airpods "
                 "launch event"),
              _m("the fruit", "fruit", "fruit eat calories tree vitamin juice pie variety orchard nutrition")),
    "amazon": (_m("the company", "company", "prime order delivery aws stock bezos package kindle seller return "
                  "account"),
               _m("the rainforest and river", "rainforest", "rainforest river forest basin brazil deforestation "
                  "tribe tribes wildlife")),
    "mercury": (_m("the planet", "planet", "planet orbit sun nasa mission retrograde surface temperature"),
                _m("the chemical element", "element", "element metal toxic poisoning thermometer liquid hg"),
                _m("Freddie Mercury", "freddie", "freddie queen singer song songs band")),
    "java": (_m("the programming language", "programming", "code coding jdk jvm spring class install version "
                "android error"),
             _m("the island", "island", "island indonesia jakarta volcano travel population")),
    "rust": (_m("the programming language", "programming", "cargo crate code coding compile borrow async tokio "
                "language memory safety"),
             _m("corrosion", "corrosion", "iron metal remove corrosion steel prevent oxide stain")),
    "shell": (_m("the command line", "command line", "bash powershell command terminal script zsh prompt"),
              _m("the oil company", "oil company", "oil petrol fuel station gas price company shares"),
              _m("a seashell", "seashell", "beach sea snail ocean")),
    "swift": (_m("the programming language", "programming", "ios xcode code coding app apps"),
              _m("Taylor Swift", "taylor", "taylor album tour song songs singer concert"),
              _m("the bird", "bird", "bird birds fly nest")),
    "mouse": (_m("the animal", "animal", "animal mice pest trap rodent eat"),
              _m("the computer mouse", "computer", "click wireless usb dpi computer gaming cursor")),
    "virus": (_m("the infection", "infection", "infection flu covid vaccine symptoms disease spread cold fever"),
              _m("a computer virus", "computer", "antivirus malware computer windows infected pc remove hacker")),
    "tesla": (_m("the car company", "company", "car cars model musk stock shares electric charging autopilot"),
              _m("Nikola Tesla", "nikola", "nikola inventor coil scientist invention edison"),
              _m("the unit of magnetic field", "unit", "unit magnetic field flux mri")),
    "bat": (_m("the animal", "animal", "animal fly flying cave echolocation species wings"),
            _m("a sports bat", "sports", "cricket baseball swing ball hit")),
    "crane": (_m("the bird", "bird", "bird birds migration species wings"),
              _m("the lifting machine", "machine", "construction lift tower load operator")),
    "bass": (_m("the low musical sound", "music", "guitar music sound speaker note notes player"),
             _m("the fish", "fish", "fish fishing catch lake")),
    "cell": (_m("the living cell", "biology", "biology organism nucleus membrane dna division"),
             _m("a mobile phone", "phone", "phone mobile number signal call"),
             _m("a battery cell", "battery", "battery voltage lithium charge")),
    "delta": (_m("the airline", "airline", "flight flights airline ticket baggage airport"),
              _m("a river delta", "river", "river sediment mouth nile mississippi")),
    "bank": (_m("the money kind", "finance", "money account loan interest deposit credit card branch"),
             _m("a river bank", "river", "river erosion flood shore")),
    "pitch": (_m("the sound's height", "sound", "sound note music voice frequency tone"),
              _m("a sports pitch", "sports", "football cricket field grass stadium"),
              _m("a sales pitch", "sales", "sales investor startup deck business")),
    "spring": (_m("the season", "season", "season weather march april flowers bloom"),
               _m("the Java framework", "framework", "java boot framework bean annotation"),
               _m("a coil spring", "mechanical", "coil compression tension force hooke")),
}
_WORD = re.compile(r"[a-z][a-z0-9-]*")


def _clue_score(meaning: Meaning, words: set[str]) -> int:
    return sum(1 for clue in meaning.clues if clue in words)


def resolve(question: str, context: str = "") -> list[Reading]:
    """How each word with several meanings in the question should be read, and on what evidence."""
    words = set(_WORD.findall(question.casefold()))
    context_words = set(_WORD.findall(str(context).casefold())) - words
    readings: list[Reading] = []
    for word, meanings in MEANINGS.items():
        if word not in words and word + "s" not in words:
            continue
        # The question already names the meaning ("python snake", "apple company"): nothing to decide.
        if any(set(meaning.qualifier.split()) <= words for meaning in meanings):
            continue
        scores = [(_clue_score(meaning, words), index) for index, meaning in enumerate(meanings)]
        best, index = max(scores)
        tied = [item for item in scores if item[0] == best]
        if best > 0 and len(tied) == 1:
            chosen_by = "question"
        else:
            context_scores = [(_clue_score(meaning, context_words), position) for position, meaning in enumerate(meanings)]
            context_best, context_index = max(context_scores)
            if context_best > 0 and sum(1 for item in context_scores if item[0] == context_best) == 1:
                index, chosen_by = context_index, "conversation"
            else:
                index, chosen_by = 0, "most common"
        meaning = meanings[index]
        readings.append(Reading(word, meaning, chosen_by,
                                tuple(other.name for other in meanings if other is not meaning)))
    return readings


def search_text(question: str, readings: list[Reading]) -> str:
    """The question with each chosen meaning's qualifier added, for search queries only."""
    text = question
    for reading in readings:
        text = re.sub(rf"(?i)\b({re.escape(reading.word)}s?)\b", rf"\1 {reading.meaning.qualifier}", text, count=1)
    return text
