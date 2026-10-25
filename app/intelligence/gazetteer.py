"""Place names in report text -> approximate coordinates, saying how precise they are.

News reports name places, not coordinates. This finds the most specific place a
headline mentions (a city first, else a country) and returns its approximate centre
with a precision label, so a country-level location is never mistaken for an exact one.
"""

from __future__ import annotations

import re
from typing import Any

#: Cities that recur in conflict, disaster and security reporting: name -> (lat, lon, country).
CITIES: dict[str, tuple[float, float, str]] = {
    "kyiv": (50.45, 30.52, "Ukraine"), "kiev": (50.45, 30.52, "Ukraine"), "kharkiv": (49.99, 36.23, "Ukraine"),
    "odesa": (46.48, 30.72, "Ukraine"), "odessa": (46.48, 30.72, "Ukraine"), "dnipro": (48.46, 35.05, "Ukraine"),
    "zaporizhzhia": (47.84, 35.14, "Ukraine"), "kherson": (46.64, 32.62, "Ukraine"), "lviv": (49.84, 24.03, "Ukraine"),
    "donetsk": (48.02, 37.80, "Ukraine"), "luhansk": (48.57, 39.33, "Ukraine"), "mariupol": (47.10, 37.55, "Ukraine"),
    "sumy": (50.91, 34.80, "Ukraine"), "pokrovsk": (48.28, 37.18, "Ukraine"), "bakhmut": (48.59, 38.00, "Ukraine"),
    "moscow": (55.76, 37.62, "Russia"), "belgorod": (50.60, 36.59, "Russia"), "kursk": (51.73, 36.19, "Russia"),
    "crimea": (45.30, 34.10, "Ukraine"), "sevastopol": (44.62, 33.53, "Ukraine"),
    "gaza": (31.50, 34.47, "Palestine"), "rafah": (31.30, 34.25, "Palestine"), "khan younis": (31.35, 34.31, "Palestine"),
    "jabalia": (31.53, 34.48, "Palestine"), "west bank": (31.95, 35.25, "Palestine"), "jenin": (32.46, 35.30, "Palestine"),
    "nablus": (32.22, 35.26, "Palestine"), "ramallah": (31.90, 35.20, "Palestine"), "jerusalem": (31.78, 35.22, "Israel"),
    "tel aviv": (32.08, 34.78, "Israel"), "haifa": (32.79, 34.99, "Israel"), "beirut": (33.89, 35.50, "Lebanon"),
    "damascus": (33.51, 36.29, "Syria"), "aleppo": (36.20, 37.16, "Syria"), "idlib": (35.93, 36.63, "Syria"),
    "homs": (34.73, 36.71, "Syria"), "baghdad": (33.31, 44.37, "Iraq"), "mosul": (36.34, 43.13, "Iraq"),
    "erbil": (36.19, 44.01, "Iraq"), "tehran": (35.69, 51.39, "Iran"), "isfahan": (32.65, 51.67, "Iran"),
    "sanaa": (15.37, 44.19, "Yemen"), "aden": (12.79, 45.03, "Yemen"), "hodeidah": (14.80, 42.95, "Yemen"),
    "red sea": (20.0, 38.5, ""), "riyadh": (24.71, 46.68, "Saudi Arabia"), "amman": (31.95, 35.93, "Jordan"),
    "cairo": (30.04, 31.24, "Egypt"), "khartoum": (15.50, 32.56, "Sudan"), "el fasher": (13.63, 25.35, "Sudan"),
    "darfur": (13.5, 24.5, "Sudan"), "omdurman": (15.64, 32.48, "Sudan"), "port sudan": (19.62, 37.22, "Sudan"),
    "tripoli": (32.89, 13.19, "Libya"), "benghazi": (32.12, 20.09, "Libya"), "mogadishu": (2.05, 45.32, "Somalia"),
    "addis ababa": (9.03, 38.74, "Ethiopia"), "tigray": (14.0, 38.5, "Ethiopia"), "amhara": (11.5, 38.0, "Ethiopia"),
    "goma": (-1.68, 29.22, "DR Congo"), "kinshasa": (-4.32, 15.31, "DR Congo"), "bamako": (12.64, -8.00, "Mali"),
    "niamey": (13.51, 2.11, "Niger"), "ouagadougou": (12.37, -1.52, "Burkina Faso"), "abuja": (9.08, 7.40, "Nigeria"),
    "lagos": (6.52, 3.38, "Nigeria"), "maiduguri": (11.85, 13.16, "Nigeria"), "juba": (4.86, 31.57, "South Sudan"),
    "nairobi": (-1.29, 36.82, "Kenya"), "kabul": (34.53, 69.17, "Afghanistan"), "kandahar": (31.63, 65.71, "Afghanistan"),
    "islamabad": (33.68, 73.05, "Pakistan"), "karachi": (24.86, 67.01, "Pakistan"), "lahore": (31.55, 74.34, "Pakistan"),
    "peshawar": (34.01, 71.58, "Pakistan"), "quetta": (30.18, 66.98, "Pakistan"), "kashmir": (34.08, 74.80, "India"),
    "new delhi": (28.61, 77.21, "India"), "delhi": (28.61, 77.21, "India"), "mumbai": (19.08, 72.88, "India"),
    "hyderabad": (17.39, 78.49, "India"), "manipur": (24.66, 93.91, "India"), "dhaka": (23.81, 90.41, "Bangladesh"),
    "yangon": (16.84, 96.17, "Myanmar"), "naypyidaw": (19.76, 96.08, "Myanmar"), "rakhine": (20.1, 93.5, "Myanmar"),
    "taipei": (25.03, 121.57, "Taiwan"), "beijing": (39.90, 116.40, "China"), "shanghai": (31.23, 121.47, "China"),
    "hong kong": (22.32, 114.17, "China"), "pyongyang": (39.04, 125.76, "North Korea"), "seoul": (37.57, 126.98, "South Korea"),
    "tokyo": (35.68, 139.69, "Japan"), "manila": (14.60, 120.98, "Philippines"), "south china sea": (12.0, 114.0, ""),
    "port-au-prince": (18.59, -72.31, "Haiti"), "caracas": (10.48, -66.90, "Venezuela"), "bogota": (4.71, -74.07, "Colombia"),
    "mexico city": (19.43, -99.13, "Mexico"), "culiacan": (24.81, -107.39, "Mexico"), "washington": (38.90, -77.04, "United States"),
    "new york": (40.71, -74.01, "United States"), "los angeles": (34.05, -118.24, "United States"),
    "london": (51.51, -0.13, "United Kingdom"), "paris": (48.86, 2.35, "France"), "berlin": (52.52, 13.40, "Germany"),
    "brussels": (50.85, 4.35, "Belgium"), "warsaw": (52.23, 21.01, "Poland"), "minsk": (53.90, 27.56, "Belarus"),
    "istanbul": (41.01, 28.98, "Turkey"), "ankara": (39.93, 32.86, "Turkey"), "tbilisi": (41.72, 44.79, "Georgia"),
    "yerevan": (40.18, 44.51, "Armenia"), "baku": (40.41, 49.87, "Azerbaijan"), "nagorno-karabakh": (39.82, 46.75, "Azerbaijan"),
    "makkah": (21.42, 39.83, "Saudi Arabia"), "mecca": (21.42, 39.83, "Saudi Arabia"), "detroit": (42.33, -83.05, "United States"),
}

#: Countries: name -> approximate centre (lat, lon). Aliases map to the same centre.
COUNTRIES: dict[str, tuple[float, float]] = {
    "afghanistan": (33.9, 67.7), "albania": (41.2, 20.2), "algeria": (28.0, 1.7), "angola": (-11.2, 17.9),
    "argentina": (-38.4, -63.6), "armenia": (40.1, 45.0), "australia": (-25.3, 133.8), "austria": (47.5, 14.6),
    "azerbaijan": (40.1, 47.6), "bahrain": (26.0, 50.6), "bangladesh": (23.7, 90.4), "belarus": (53.7, 27.9),
    "belgium": (50.5, 4.5), "benin": (9.3, 2.3), "bolivia": (-16.3, -63.6), "bosnia": (43.9, 17.7), "botswana": (-22.3, 24.7),
    "brazil": (-14.2, -51.9), "bulgaria": (42.7, 25.5), "burkina faso": (12.2, -1.6), "burundi": (-3.4, 29.9),
    "cambodia": (12.6, 104.9), "cameroon": (7.4, 12.4), "canada": (56.1, -106.3), "central african republic": (6.6, 20.9),
    "chad": (15.5, 18.7), "chile": (-35.7, -71.5), "china": (35.9, 104.2), "colombia": (4.6, -74.3), "congo": (-0.2, 15.8),
    "dr congo": (-4.0, 21.8), "democratic republic of the congo": (-4.0, 21.8), "costa rica": (9.7, -83.8),
    "croatia": (45.1, 15.2), "cuba": (21.5, -77.8), "cyprus": (35.1, 33.4), "czech republic": (49.8, 15.5),
    "denmark": (56.3, 9.5), "djibouti": (11.8, 42.6), "dominican republic": (18.7, -70.2), "ecuador": (-1.8, -78.2),
    "egypt": (26.8, 30.8), "el salvador": (13.8, -88.9), "eritrea": (15.2, 39.8), "estonia": (58.6, 25.0),
    "ethiopia": (9.1, 40.5), "finland": (61.9, 25.7), "france": (46.2, 2.2), "gabon": (-0.8, 11.6), "gambia": (13.4, -15.3),
    "georgia": (42.3, 43.4), "germany": (51.2, 10.5), "ghana": (7.9, -1.0), "greece": (39.1, 21.8), "guatemala": (15.8, -90.2),
    "guinea": (9.9, -9.7), "haiti": (18.97, -72.3), "honduras": (15.2, -86.2), "hungary": (47.2, 19.5), "iceland": (64.96, -19.0),
    "india": (20.6, 79.0), "indonesia": (-0.8, 113.9), "iran": (32.4, 53.7), "iraq": (33.2, 43.7), "ireland": (53.4, -8.2),
    "israel": (31.0, 34.9), "italy": (41.9, 12.6), "ivory coast": (7.5, -5.5), "jamaica": (18.1, -77.3), "japan": (36.2, 138.3),
    "jordan": (30.6, 36.2), "kazakhstan": (48.0, 66.9), "kenya": (-0.02, 37.9), "kosovo": (42.6, 20.9), "kuwait": (29.3, 47.5),
    "kyrgyzstan": (41.2, 74.8), "laos": (19.9, 102.5), "latvia": (56.9, 24.6), "lebanon": (33.9, 35.9), "liberia": (6.4, -9.4),
    "libya": (26.3, 17.2), "lithuania": (55.2, 23.9), "madagascar": (-18.8, 46.9), "malawi": (-13.3, 34.3),
    "malaysia": (4.2, 102.0), "mali": (17.6, -4.0), "mauritania": (21.0, -10.9), "mexico": (23.6, -102.6),
    "moldova": (47.4, 28.4), "mongolia": (46.9, 103.8), "montenegro": (42.7, 19.4), "morocco": (31.8, -7.1),
    "mozambique": (-18.7, 35.5), "myanmar": (21.9, 95.96), "burma": (21.9, 95.96), "namibia": (-22.96, 18.5),
    "nepal": (28.4, 84.1), "netherlands": (52.1, 5.3), "new zealand": (-40.9, 174.9), "nicaragua": (12.9, -85.2),
    "niger": (17.6, 8.1), "nigeria": (9.1, 8.7), "north korea": (40.3, 127.5), "north macedonia": (41.6, 21.7),
    "norway": (60.5, 8.5), "oman": (21.5, 55.9), "pakistan": (30.4, 69.3), "palestine": (31.9, 35.2),
    "panama": (8.5, -80.8), "papua new guinea": (-6.3, 143.96), "paraguay": (-23.4, -58.4), "peru": (-9.2, -75.0),
    "philippines": (12.9, 121.8), "poland": (51.9, 19.1), "portugal": (39.4, -8.2), "qatar": (25.4, 51.2),
    "romania": (45.9, 24.97), "russia": (61.5, 105.3), "rwanda": (-1.9, 29.9), "saudi arabia": (23.9, 45.1),
    "senegal": (14.5, -14.5), "serbia": (44.0, 21.0), "sierra leone": (8.5, -11.8), "singapore": (1.35, 103.8),
    "slovakia": (48.7, 19.7), "slovenia": (46.2, 14.995), "somalia": (5.2, 46.2), "south africa": (-30.6, 22.9),
    "south korea": (35.9, 127.8), "south sudan": (6.9, 31.3), "spain": (40.5, -3.7), "sri lanka": (7.9, 80.8),
    "sudan": (12.9, 30.2), "sweden": (60.1, 18.6), "switzerland": (46.8, 8.2), "syria": (34.8, 39.0), "taiwan": (23.7, 121.0),
    "tajikistan": (38.9, 71.3), "tanzania": (-6.4, 34.9), "thailand": (15.9, 100.99), "togo": (8.6, 0.8),
    "tunisia": (33.9, 9.5), "turkey": (38.96, 35.2), "turkiye": (38.96, 35.2), "turkmenistan": (38.97, 59.6),
    "uganda": (1.4, 32.3), "ukraine": (48.4, 31.2), "united arab emirates": (23.4, 53.8), "uae": (23.4, 53.8),
    "united kingdom": (55.4, -3.4), "uk": (55.4, -3.4), "britain": (55.4, -3.4), "united states": (37.1, -95.7),
    "us": (37.1, -95.7), "usa": (37.1, -95.7), "uruguay": (-32.5, -55.8), "uzbekistan": (41.4, 64.6),
    "venezuela": (6.4, -66.6), "vietnam": (14.1, 108.3), "yemen": (15.6, 48.5), "zambia": (-13.1, 27.8),
    "zimbabwe": (-19.0, 29.2),
}
#: Demonyms and adjectives that point at a country in headlines ("Israeli strike", "Russian drones").
ADJECTIVES = {"ukrainian": "ukraine", "russian": "russia", "israeli": "israel", "palestinian": "palestine",
              "lebanese": "lebanon", "syrian": "syria", "iraqi": "iraq", "iranian": "iran", "yemeni": "yemen",
              "sudanese": "sudan", "somali": "somalia", "afghan": "afghanistan", "pakistani": "pakistan",
              "indian": "india", "chinese": "china", "taiwanese": "taiwan", "korean": "south korea",
              "haitian": "haiti", "congolese": "dr congo", "malian": "mali", "nigerian": "nigeria",
              "ethiopian": "ethiopia", "libyan": "libya", "turkish": "turkey", "egyptian": "egypt",
              "mexican": "mexico", "venezuelan": "venezuela", "colombian": "colombia", "burmese": "myanmar",
              "american": "united states", "british": "united kingdom", "french": "france", "german": "germany",
              "polish": "poland", "saudi": "saudi arabia", "houthi": "yemen", "japanese": "japan", "filipino": "philippines"}
_CITY_PATTERN = re.compile(r"(?i)\b(" + "|".join(sorted((re.escape(name) for name in CITIES), key=len, reverse=True)) + r")\b")
_COUNTRY_PATTERN = re.compile(r"(?i)\b(" + "|".join(sorted((re.escape(name) for name in COUNTRIES if len(name) > 3),
                                                             key=len, reverse=True)) + r"|US|UK|UAE)\b")
_ADJECTIVE_PATTERN = re.compile(r"(?i)\b(" + "|".join(ADJECTIVES) + r")\b")


def locate(text: str) -> dict[str, Any] | None:
    """The most specific place a text names: {latitude, longitude, place, country, precision} or None.

    A city or region beats a country; a named country beats one implied by an adjective
    ("Israeli strike on Beirut" is located in Beirut, not Israel).
    """
    value = str(text or "")
    if match := _CITY_PATTERN.search(value):
        latitude, longitude, country = CITIES[match[1].lower()]
        return {"latitude": latitude, "longitude": longitude, "place": match[1].title(), "country": country,
                "precision": "city or region (approximate centre)"}
    if match := _COUNTRY_PATTERN.search(value):
        name = match[1].lower()
        name = {"us": "united states", "uk": "united kingdom", "uae": "united arab emirates"}.get(name, name)
        latitude, longitude = COUNTRIES[name]
        return {"latitude": latitude, "longitude": longitude, "place": name.title(), "country": name.title(),
                "precision": "country (centre of the country, not the event)"}
    if match := _ADJECTIVE_PATTERN.search(value):
        name = ADJECTIVES[match[1].lower()]
        latitude, longitude = COUNTRIES[name]
        return {"latitude": latitude, "longitude": longitude, "place": name.title(), "country": name.title(),
                "precision": "country implied by an adjective (low)"}
    return None


def country_of(text: str) -> str:
    found = locate(text)
    return (found or {}).get("country", "")
