"""Handlers with planted defects, for tests/test_fuzz_lab.py. Not collected as tests."""
import time


def planted_crash(text: str) -> int:
    """Breaks its contract on one narrow input: a KeyError when the text holds both a tab and the word 'system:'."""
    if "\t" in text and "system:" in text:
        return {}["missing"]
    if not text:
        raise ValueError("empty")          # part of the contract
    return len(text)


def planted_property(text: str) -> str:
    """Says it removes every zero-width space; it removes only the first."""
    return text.replace("​", "", 1)


def no_zero_width_left(text: str, result: str) -> str:
    return "zero-width space left in the result" if "​" in result else ""


def planted_slow(text: str) -> int:
    if "((((" in text:
        time.sleep(0.4)
    return 0


def planted_hang(text: str) -> int:
    while "\x00" in text:                  # never ends once a NUL is present
        pass
    return 0


def sound(text: str) -> str:
    return text.strip().lower()
