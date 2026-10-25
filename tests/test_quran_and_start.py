"""The owner's "start ..." requests, and reading the Quran from the published text.

2026-10-05 history: "start earning" and "start quran from starting with roman
english so that i can read" were both answered "... isn't on my approved
application list"; "use the camera and test it" ended "returned nothing to show".
"""

from __future__ import annotations

import pytest

from app.intent.router import IntentRouter
from app.observed_reply import render
from app.session import SessionState
from app.tools.quran import CONTINUE_HINT, QuranReader, direct_quran_plan, quran_request


def _surah_payload():
    def edition(texts):
        return {"number": 1, "name": "سُورَةُ ٱلْفَاتِحَةِ", "englishName": "Al-Faatiha",
                "englishNameTranslation": "The Opening", "numberOfAyahs": 3, "revelationType": "Meccan",
                "ayahs": [{"number": index + 1, "numberInSurah": index + 1, "text": text} for index, text in enumerate(texts)]}
    return {"code": 200, "data": [
        edition(["﻿بِسْمِ ٱللَّهِ", "ٱلْحَمْدُ لِلَّهِ", "ٱلرَّحْمَٰنِ"]),
        edition(["Bismillaahir Rahmaanir Raheem", "Alhamdu lillaahi Rabbil 'aalameen", "Ar-Rahmaanir-Raheem"]),
        edition(["In the name of Allah", "[All] praise is [due] to Allah", "The Entirely Merciful"]),
    ]}


SURAHS = {"code": 200, "data": [
    {"number": 1, "englishName": "Al-Faatiha", "englishNameTranslation": "The Opening", "numberOfAyahs": 3},
    {"number": 2, "englishName": "Al-Baqara", "englishNameTranslation": "The Cow", "numberOfAyahs": 286},
    {"number": 103, "englishName": "Al-Asr", "englishNameTranslation": "The Declining Day", "numberOfAyahs": 3},
]}


@pytest.fixture
def reader(tmp_path):
    calls = []

    def get_json(url):
        calls.append(url)
        if url.endswith("/surah"):
            return SURAHS
        if "/surah/1/editions/" in url:
            return _surah_payload()
        raise OSError("offline")

    value = QuranReader(get_json, tmp_path / "quran")
    value.calls = calls
    return value


def test_ayahs_come_from_the_published_text_and_are_cached(reader):
    result = reader.read("fatiha", 1, 2)
    assert [a["transliteration"] for a in result["ayahs"]] == ["Bismillaahir Rahmaanir Raheem",
                                                               "Alhamdu lillaahi Rabbil 'aalameen"]
    assert result["ayahs"][0]["arabic"] == "بِسْمِ ٱللَّهِ"  # byte-order mark removed
    assert result["next"] == {"surah": 1, "ayah": 3} and result["range"] == "1:1-2"
    reader.read(1, 1, 1)
    assert sum("/editions/" in url for url in reader.calls) == 1  # second read is from the cache
    following = reader.continue_reading(5)
    assert following["range"] == "1:2-3" and following["next"] == {"surah": 2, "ayah": 1}
    text = render(result)
    assert text.startswith("Surah Al-Faatiha (The Opening) · 1:1-2 of 3 ayahs")
    assert "1. Bismillaahir Rahmaanir Raheem" in text and "Saheeh International" in text and CONTINUE_HINT in text


def test_the_bismillah_prefix_is_split_from_ayah_one_of_other_surahs(tmp_path):
    payload = _surah_payload()
    for edition in payload["data"]:
        edition["number"], edition["englishName"] = 112, "Al-Ikhlaas"
    # The service's own encoding of the marks differs from the constant; letters decide.
    payload["data"][0]["ayahs"][0]["text"] = "﻿بِسۡمِ ٱللَّهِ ٱلرَّحۡمَٰنِ ٱلرَّحِيمِ قُلْ هُوَ ٱللَّهُ أَحَدٌ"
    reader = QuranReader(lambda url: SURAHS if url.endswith("/surah") else payload, tmp_path)
    result = reader.read(112, 1, 1)
    assert result["ayahs"][0]["arabic"] == "قُلْ هُوَ ٱللَّهُ أَحَدٌ"
    assert result["bismillah"]["transliteration"] == "Bismillaahir Rahmaanir Raheem"
    assert render(result).splitlines()[2] == "Bismillaahir Rahmaanir Raheem"


def test_surah_names_resolve_without_eating_real_letters(reader):
    assert reader.resolve("Al-Fatiha") == reader.resolve("alfatiha") == reader.resolve("fatihah") == 1
    assert reader.resolve("asr") == reader.resolve("al asr") == 103
    assert reader.resolve("yaseen") == reader.resolve("Yasin") == 36
    with pytest.raises(ValueError):
        reader.resolve(115)


def test_quran_requests_are_recognised_and_questions_are_not():
    assert quran_request("start quran from starting with roman english so that i can read") == {}
    assert quran_request("read surah yasin") == {"surah": "yasin"}
    assert quran_request("recite surah 36 ayah 5") == {"surah": 36, "start_ayah": 5}
    assert quran_request("read quran 2:255") == {"surah": 2, "start_ayah": 255}
    assert quran_request("continue quran") == {"continue": True}
    assert quran_request("continue") is None
    assert quran_request("continue", "... " + CONTINUE_HINT) == {"continue": True}
    assert quran_request("what does the quran say about patience") is None
    plan = direct_quran_plan("read surah yasin", ["quran_read"])
    assert plan[0]["tool"] == "quran_read" and plan[0]["arguments"] == {"surah": "yasin"}


def test_start_is_not_an_application_launch_unless_it_names_one():
    router, state = IntentRouter(None), SessionState()
    quran = router.route("start quran from starting with roman english so that i can read", state)
    assert quran.entities == {"quran": {}} and not quran.needs_clarification
    earning = router.route("start earning", state)
    assert "approved application list" not in (earning.response or "")
    assert "business_ideas" in earning.entities
    unknown = router.route("open frobnicator", state)
    assert "approved application list" in (unknown.response or "")  # a real unknown program is still refused


def test_camera_status_is_described_instead_of_nothing_to_show():
    text = render({"state": "OFF", "active": False, "devices_detected": 1, "background_recording": False,
                   "last_error": None, "ocr": {"available": True}})
    assert text.startswith("Camera is off; 1 camera detected.") and "turn on the camera" in text


def test_reader_rejects_a_failed_service_reply(tmp_path):
    reader = QuranReader(lambda url: {"code": 500, "data": "error"}, tmp_path)
    with pytest.raises(ValueError, match="did not return the text"):
        reader.read(1)
    assert not (tmp_path / "surah-001.json").exists()
