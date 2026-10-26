"""Does a job's advertised location match the place the owner asked for?

Job boards write places many ways: "Detroit, MI", "Remote - US", "Bengaluru,
Karnataka, India", "NYC". Matching the filter text word for word failed 17 of
25 realistic cases on 2026-10-25: "Michigan" missed "Detroit, MI", "USA"
missed every US posting that names only a city and state, "Bangalore" missed
"Bengaluru", and "Michigan" wrongly matched "Michigan City, IN".

The rules here:
- a state or province matches its name or its postal code as a whole part of
  the location ("MI" in "Detroit, MI"), never as a word inside a city name;
- a country also matches postings that name one of its states or provinces;
- cities match their common other names (Bangalore = Bengaluru, NYC = New York);
- "Remote" matches remote postings however they are written;
- "City, State" in the filter needs every part to match.
"""
from __future__ import annotations

import re

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "ON": "Ontario", "PE": "Prince Edward Island",
    "QC": "Quebec", "SK": "Saskatchewan", "NT": "Northwest Territories", "NU": "Nunavut", "YT": "Yukon",
}
#: Other names for the same city. Each group is one place.
CITY_ALIASES = (
    {"new york", "new york city", "nyc"}, {"san francisco", "sf"}, {"washington dc", "washington d c", "dc"},
    {"bangalore", "bengaluru"}, {"mumbai", "bombay"}, {"gurgaon", "gurugram"}, {"chennai", "madras"},
    {"kolkata", "calcutta"}, {"pune", "poona"}, {"ho chi minh city", "saigon"}, {"beijing", "peking"},
    {"dubai", "dubayy"}, {"kyiv", "kiev"},
)
_REMOTE = re.compile(r"(?i)\b(?:remote|anywhere|work\s+from\s+home|wfh|distributed|worldwide)\b")


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", str(text or "").casefold()).strip()


def _segments(place: str) -> list[str]:
    """'Toronto, ON (Hybrid) / Remote - Canada' -> ['toronto', 'on', 'hybrid', 'remote', 'canada']."""
    return [part for part in (_norm(piece) for piece in re.split(r"[,;/|()\-–—]+", str(place or ""))) if part]


def _state_codes(names: dict[str, str]) -> dict[str, str]:
    return {name.casefold(): code for code, name in names.items()}


_US_BY_NAME, _CA_BY_NAME = _state_codes(US_STATES), _state_codes(CA_PROVINCES)


def _subdivision(want: str) -> tuple[str, str, str] | None:
    """('US', 'MI', 'michigan') for "Michigan" or "MI"; None when it is not a state or province."""
    value = _norm(want)
    for country, codes, by_name in (("US", US_STATES, _US_BY_NAME), ("Canada", CA_PROVINCES, _CA_BY_NAME)):
        if value.upper() in codes and len(value) == 2:
            return country, value.upper(), codes[value.upper()].casefold()
        if value in by_name:
            return country, by_name[value], value
    return None


def _countries_in(place: str) -> set[str]:
    """Countries a posting names, including those implied by a state or province ("Detroit, MI" -> US)."""
    from app.jobs.understand import REGIONS

    found = {name for name, pattern in REGIONS.items() if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", str(place or ""), re.I)}
    for part in (piece.strip() for piece in re.split(r"[,;/|()\-–—]+", str(place or "")) if piece.strip()):
        if part in US_STATES or part.casefold() in _US_BY_NAME:
            if not (part == "IN" and "India" in found):  # "IN" is Indiana unless the posting says India
                found.add("US")
        if part in CA_PROVINCES or part.casefold() in _CA_BY_NAME:
            found.add("Canada")
    return found


def _country(want: str) -> str | None:
    from app.jobs.understand import REGIONS

    for name, pattern in REGIONS.items():
        if re.fullmatch(rf"(?:{pattern})", str(want or "").strip(), re.IGNORECASE):
            return name
    return None


def _aliases(want: str) -> set[str]:
    value = _norm(want)
    for group in CITY_ALIASES:
        if value in group:
            return set(group)
    return {value}


#: Countries inside the job boards' "EU" region, so an EU-only remote job counts for them.
_EU = {"Germany", "Netherlands", "France", "Spain", "Poland"}


def country_of(want: str) -> str | None:
    """The country a filter place belongs to: "Michigan" -> "US", "Ontario" -> "Canada", "India" -> "India"."""
    value = str(want or "").strip()
    if "," in value:  # "Detroit, MI": the most specific part that names a country
        for part in reversed(value.split(",")):
            found = country_of(part)
            if found:
                return found
        return None
    subdivision = _subdivision(value)
    return subdivision[0] if subdivision else _country(value)


def remote_open_to(place: str, want: str, *, regions: list[str] | tuple[str, ...] = ()) -> bool:
    """Whether a *remote* posting may be done from the filter place.

    "data analyst in Michigan" found 114 jobs and showed none: the free boards are mostly remote, and 96 of those
    jobs were remote, 54 of them open to US residents. Someone in Michigan can do them. A remote job with no stated
    restriction, or one open worldwide, counts everywhere; one restricted to a region counts for its countries.
    """
    allowed = set(regions or ()) | _countries_in(place)
    allowed.discard("")
    if "Worldwide" in allowed:
        return True
    if not allowed:
        # Unrestricted only when the posting names no place at all ("Remote", "Anywhere", blank). A place we do
        # not recognise ("Portugal", "Panama") is a restriction to that place, not an open door.
        leftover = re.sub(r"[^A-Za-z]+", " ", _REMOTE.sub(" ", str(place or ""))).strip()
        return not leftover
    country = country_of(want)
    if country is None:
        return False
    return country in allowed or ("EU" in allowed and country in _EU)


def place_matches(place: str, want: str, *, regions: list[str] | tuple[str, ...] = (), remote: bool = False) -> bool:
    """Whether a posting's location `place` satisfies the filter `want` (see the module rules)."""
    value = str(want or "").strip()
    if not value:
        return True
    if "," in value:  # "Paris, TX": every part must match
        parts = [part for part in value.split(",") if part.strip()]
        return all(place_matches(place, part, regions=regions, remote=remote) for part in parts)
    if _norm(value) == "remote":
        return remote or bool(_REMOTE.search(str(place or "")))
    text = " " + " ".join(_segments(place)) + " "
    aliases = _aliases(value)
    known_city = len(aliases) > 1  # "New York" is a state and a city: NYC counts too
    subdivision = _subdivision(value)
    if subdivision:
        _country_name, code, name = subdivision
        raw_parts = [part.strip() for part in re.split(r"[,;/|()\-–—]+", str(place or "")) if part.strip()]
        if any(part == code or _norm(part) == name for part in raw_parts):
            return True
        return known_city and any(f" {alias} " in text for alias in aliases)
    country = _country(value)
    if country:
        return country in _countries_in(place) or country in (regions or ())
    return any(f" {alias} " in text for alias in aliases)
