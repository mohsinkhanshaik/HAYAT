"""A failed video download explains why in plain words, without hiding HAYAT's own clear errors."""
from __future__ import annotations

import pytest

from app.video_workflow import classify_download_error


@pytest.mark.parametrize("raw, expect", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content", "members-only"),
    ("ERROR: Sign in to confirm your age. This video may be inappropriate for some users.", "age-verified"),
    ("ERROR: Video unavailable. This video is no longer available", "unavailable or has been removed"),
    ("ERROR: The uploader has not made this video available in your country", "blocked in this location"),
    ("ERROR: Requested format is not available. Use --list-formats", "quality was not available"),
    ("ERROR: ffmpeg not found. Please install or provide the path with --ffmpeg-location", "needs FFmpeg"),
    ("ERROR: HTTP Error 429: Too Many Requests", "rate-limiting"),
    ("ERROR: unable to download webpage: HTTP Error 403: Forbidden", "refused the download"),
    ("ERROR: Unsupported URL: https://example.com/not-a-video", "not a supported video page"),
    ("socket.timeout: The read operation timed out", "timed out"),
])
def test_known_failures_become_plain_reasons(raw, expect):
    message = classify_download_error(raw)
    assert expect.lower() in message.lower()
    assert "ERROR:" not in message and "--" not in message  # no raw yt-dlp noise


def test_our_own_clear_worker_messages_pass_through_unchanged():
    for message in (
        "Use a single video link, not a playlist",
        "Choose a completed video; live streams are not downloaded",
        "Video download exceeded its total byte limit",
    ):
        assert classify_download_error(message) == message


def test_empty_error_has_a_safe_default():
    assert classify_download_error("") == "That video could not be downloaded."


def test_403_is_classified_even_though_it_mentions_webpage_download():
    # The 403 rule must win over the generic "unable to download webpage" rule.
    assert "refused the download" in classify_download_error(
        "ERROR: unable to download webpage: HTTP Error 403: Forbidden").lower()
