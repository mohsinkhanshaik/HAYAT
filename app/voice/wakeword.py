from __future__ import annotations


class WakeWordDetector:
    """Phrase gate for optional wake mode; audio never leaves the STT pipeline."""

    def __init__(self, phrase: str = "hey ai", aliases: list[str] | None = None) -> None:
        self.phrase = " ".join(phrase.lower().split())
        self.phrases = [self.phrase, *(" ".join(item.lower().split()) for item in (aliases or []))]

    def extract_command(self, transcription: str) -> str | None:
        normalized = " ".join(transcription.lower().strip().split())
        for phrase in self.phrases:
            index = normalized.find(phrase)
            if index >= 0:
                return normalized[index + len(phrase):].lstrip(" ,.!?:;-")
        return None
