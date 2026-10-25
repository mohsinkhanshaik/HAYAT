from __future__ import annotations

from types import SimpleNamespace

from app.media_feedback import MediaFeedback


def test_media_defaults_learn_only_after_three_owner_ratings(tmp_path):
    records = []
    for index in range(3):
        path = tmp_path / f"image-{index}.png"; path.write_bytes(b"png")
        records.append({"path": str(path), "quality": "quality", "style": "cinematic",
                        "model": "local-model.gguf"})
    generator = SimpleNamespace(history=lambda _limit: records, video_history=lambda _limit: [],
                                model_path=tmp_path / "local-model.gguf")
    feedback = MediaFeedback(generator, tmp_path / "feedback.db")
    for index, record in enumerate(records):
        assert feedback.record(record["path"], 4 + (index % 2), "good detail")["recorded"]
    recommendation = feedback.recommend("image")
    assert recommendation["trusted"] is True
    assert recommendation["quality"] == "quality" and recommendation["style"] == "cinematic"
    assert recommendation["policy"] == "owner ratings only; no model self-grading"


def test_unrecognized_media_cannot_poison_feedback(tmp_path):
    generator = SimpleNamespace(history=lambda _limit: [], video_history=lambda _limit: [], model_path=None)
    result = MediaFeedback(generator, tmp_path / "feedback.db").record(str(tmp_path / "foreign.png"), 5, "perfect")
    assert result["recorded"] is False
