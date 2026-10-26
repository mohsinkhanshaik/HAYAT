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


@pytest.mark.parametrize("place,regions,want,expected", [
    ("United States", ["US"], "Michigan", True),        # remote, open to US residents
    ("Remote", [], "Michigan", True),                   # no restriction stated
    ("Anywhere", ["Worldwide"], "India", True),
    ("Remote - EU", ["EU"], "Germany", True),           # EU-only remote counts for EU countries
    ("Remote - UK", ["UK"], "Michigan", False),         # restricted elsewhere
    ("Portugal", [], "India", False),                   # an unrecognised place is a restriction, not "anywhere"
    ("Panama", [], "Detroit", False),
])
def test_a_place_filter_includes_remote_jobs_you_can_do_from_there(place, regions, want, expected):
    row = {"location": place, "reading": {"mode": "remote", "regions": regions}}
    assert matches_location(row, want, include_remote=True) is expected
    assert matches_location(row, want) is False  # the strict rule: only jobs located at the place


def test_on_site_only_when_remote_jobs_are_not_wanted():
    remote_us = {"location": "United States", "reading": {"mode": "remote", "regions": ["US"]}}
    assert matches_location(remote_us, "Michigan", include_remote=True)
    assert not matches_location(remote_us, "Michigan", include_remote=False)
    office = {"location": "Detroit, MI", "reading": {"mode": "on-site", "regions": []}}
    assert matches_location(office, "Michigan", include_remote=False)
