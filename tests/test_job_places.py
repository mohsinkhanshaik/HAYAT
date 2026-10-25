"""The Jobs location filter understands how job boards write places (app/jobs/places.py)."""
import pytest

from app.jobs.places import place_matches
from app.jobs.sources import matches_location


@pytest.mark.parametrize("place,want,expected", [
    ("Detroit, MI, USA", "Detroit", True), ("Detroit, MI", "Michigan", True), ("Ann Arbor, MI", "Michigan", True),
    ("Detroit, MI", "USA", True), ("Detroit, MI", "United States", True), ("Austin, TX", "Texas", True),
    ("New York, NY", "New York", True), ("NYC", "New York", True), ("New York, NY", "NYC", True),
    ("San Francisco, CA", "SF", True), ("Bengaluru, Karnataka, India", "Bangalore", True),
    ("Bangalore", "Bengaluru", True), ("Mumbai, India", "Bombay", True), ("Gurugram, Haryana", "Gurgaon", True),
    ("Toronto, ON, Canada", "Ontario", True), ("Toronto, ON", "Canada", True), ("London, UK", "United Kingdom", True),
    ("London, United Kingdom", "UK", True), ("Remote - US", "Remote", True), ("Remote (Worldwide)", "Remote", True),
    ("Hyderabad, Telangana, India", "Hyderabad", True), ("Michigan City, IN", "Indiana", True),
    ("Remote - US", "United States", True), ("Berlin, Germany", "Germany", True),
    # and what must not match
    ("Detroit, MI", "Texas", False), ("Paris, France", "Paris, TX", False), ("Michigan City, IN", "Michigan", False),
    ("London, Ontario, Canada", "London, UK", False), ("Bengaluru, IN, India", "USA", False), ("Chicago, IL", "Canada", False),
])
def test_places_are_matched_the_way_job_boards_write_them(place, want, expected):
    assert place_matches(place, want) is expected


def test_the_saved_jobs_filter_uses_it_with_alternatives_and_remote_mode():
    row = {"location": "Detroit, MI", "reading": {"mode": "hybrid", "regions": []}}
    assert matches_location(row, "Michigan") and matches_location(row, "Texas; Michigan")
    assert not matches_location(row, "Texas") and matches_location(row, "Worldwide")
    remote_row = {"location": "", "reading": {"mode": "remote", "regions": ["US"]}}
    assert matches_location(remote_row, "Remote") and matches_location(remote_row, "USA")
