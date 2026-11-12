"""On the ground near a place: wildfire perimeters, whale sightings and street-level photos.

Added 2026-11-06 from the Fire Perimeters, Wildlife Sightings and Street Level layers of
github.com/bilawalsidhu/gods-eye-view (MIT). Sources, all fetched through HAYAT's fetcher with
robots.txt honoured:

| Feature | Source | Notes |
|---|---|---|
| Fire perimeters | NIFC WFIGS current interagency perimeters | US; size, containment, cause, crews |
| Whale sightings | Acartia (Salish Sea marine mammals, CC BY 4.0) | observers' comments and names are dropped, places rounded to ~1 km |
| Street photo | Panoramax (federated open street imagery) | faces and plates are blurred by Panoramax; licence and author kept |

iNaturalist, which that repository also uses for wildlife, is not: its robots.txt disallows
the API. KartaView did not answer.
"""
from __future__ import annotations

import io
import math
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.intelligence.airspace import contains, edge_km, rings
from app.intelligence.explore import bearing_deg, compass, distance_km

PERIMETERS = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query?"
              + urlencode({"where": "1=1", "outFields": "poly_IncidentName,attr_UniqueFireIdentifier,attr_IncidentSize,attr_PercentContained,"
                           "attr_POOState,attr_IncidentTypeCategory,attr_FireDiscoveryDateTime,poly_DateCurrent,attr_FireCause,"
                           "attr_TotalIncidentPersonnel,attr_POOCounty", "maxAllowableOffset": "0.001", "outSR": "4326", "f": "geojson"}))
INCIDENT_TYPES = {"WF": "wildfire", "RX": "prescribed burn", "CX": "complex"}
#: A perimeter not redrawn for this long may no longer match the fire.
PERIMETER_STALE_DAYS = 14
ACARTIA = "https://acartia.io/api/v1/sightings/current"
#: Acartia's coverage: the Salish Sea and the outer coast from Oregon to northern British Columbia.
ACARTIA_BOX = (42.0, 55.5, -132.0, -121.0)
PANORAMAX = "https://api.panoramax.xyz/api/search"
PHOTO_HOSTS = (".ovh.net", ".panoramax.xyz", ".panoramax.fr", ".openstreetmap.fr", ".ign.fr", ".panoramax.ign.fr")


def _date(milliseconds: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(milliseconds) / 1000, UTC).isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class Ground:
    def __init__(self, explore: Any) -> None:
        self.explore = explore

    # ----------------------------------------------------------------- fire perimeters
    def fire_perimeters(self, place: str = "here", radius_km: float = 150, include_prescribed: bool = True,
                        limit: int = 10) -> dict[str, Any]:
        where = self.explore._place(place)
        latitude, longitude = where["latitude"], where["longitude"]
        radius = max(5.0, min(float(radius_km), 3000.0))
        data = self.explore._json("wfigs-perimeters", PERIMETERS, 900, 32 * 1024 * 1024)
        if not isinstance(data, dict) or "features" not in data:
            raise ValueError("NIFC's perimeter service answered without perimeters.")
        now = time.time()
        fires = []
        for feature in data["features"]:
            properties, geometry = feature.get("properties") or {}, feature.get("geometry")
            kind = INCIDENT_TYPES.get(properties.get("attr_IncidentTypeCategory"), "fire")
            if kind == "prescribed burn" and not include_prescribed:
                continue
            inside = contains(geometry, latitude, longitude)
            edge = 0.0 if inside else edge_km(geometry, latitude, longitude)
            if edge is None or edge > radius:
                continue
            outline = next((polygon[0] for polygon in rings(geometry) if polygon and polygon[0]), [])
            centre = (sum(point[1] for point in outline) / len(outline), sum(point[0] for point in outline) / len(outline)) if outline else None
            updated = properties.get("poly_DateCurrent")
            age_days = (now - float(updated) / 1000) / 86400 if isinstance(updated, (int, float)) else None
            fires.append({"name": str(properties.get("poly_IncidentName") or "").strip() or "Unnamed fire", "kind": kind,
                          "acres": properties.get("attr_IncidentSize"), "contained_pct": properties.get("attr_PercentContained"),
                          "cause": properties.get("attr_FireCause"), "personnel": properties.get("attr_TotalIncidentPersonnel"),
                          "state": str(properties.get("attr_POOState") or "").replace("US-", ""), "county": properties.get("attr_POOCounty"),
                          "discovered": _date(properties.get("attr_FireDiscoveryDateTime")), "perimeter_updated": _date(updated),
                          "perimeter_stale": age_days is not None and age_days > PERIMETER_STALE_DAYS,
                          "inside": inside, "edge_km": round(edge, 1),
                          "direction": compass(bearing_deg(latitude, longitude, *centre)) if centre and not inside else None,
                          "id": properties.get("attr_UniqueFireIdentifier")})
        fires.sort(key=lambda item: (not item["inside"], item["edge_km"]))
        return {"intelligence": True, "explore": "perimeters", "place": where["name"], "radius_km": radius,
                "count": len(fires), "fires": fires[:max(1, min(int(limit), 50))], "inside": any(item["inside"] for item in fires),
                "season_total": len(data["features"]),
                "source": "NIFC WFIGS current interagency fire perimeters (U.S. public domain)",
                "basis": "Mapped perimeters as incident teams last drew them; a fire grows between updates, and perimeters "
                         f"not redrawn for {PERIMETER_STALE_DAYS} days are marked stale. US incidents only. Follow local "
                         "evacuation orders, not this map."}

    # ----------------------------------------------------------------- whales
    def whale_sightings(self, place: str = "here", radius_km: float = 60, days: int = 3, limit: int = 15) -> dict[str, Any]:
        where = self.explore._place(place)
        latitude, longitude = where["latitude"], where["longitude"]
        south, north, west, east = ACARTIA_BOX
        if not (south <= latitude <= north and west <= longitude <= east):
            raise ValueError(f"Open whale-sighting data HAYAT can read covers the Salish Sea and the Pacific Northwest coast "
                             f"(Acartia); {where['name']} is outside it.")
        radius, window = max(5.0, min(float(radius_km), 400.0)), max(1, min(int(days), 7))
        rows = self.explore._json("acartia-current", ACARTIA, 600, 8 * 1024 * 1024)
        now = datetime.now(UTC)
        sightings, species = [], {}
        for row in rows if isinstance(rows, list) else []:
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
                seen = datetime.strptime(str(row["created"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                continue
            hours = (now - seen).total_seconds() / 3600
            distance = distance_km(latitude, longitude, lat, lon)
            if distance > radius or not 0 <= hours <= window * 24 or not math.isfinite(lat + lon):
                continue
            kind = str(row.get("type") or "unidentified").strip() or "unidentified"
            species[kind] = species.get(kind, 0) + 1
            # Observers' comments carry names; only the sighting itself is kept, its place rounded to about 1 km.
            sightings.append({"species": kind, "count": row.get("no_sighted"), "seen": seen.isoformat(timespec="minutes"),
                              "hours_ago": round(hours, 1), "latitude": round(lat, 2), "longitude": round(lon, 2),
                              "distance_km": round(distance, 1), "direction": compass(bearing_deg(latitude, longitude, lat, lon)),
                              "trusted": bool(row.get("trusted")), "reported_via": row.get("data_source_name")})
        sightings.sort(key=lambda item: item["hours_ago"])
        return {"intelligence": True, "explore": "whales", "place": where["name"], "radius_km": radius, "days": window,
                "count": len(sightings), "species": sorted(species.items(), key=lambda pair: -pair[1]),
                "sightings": sightings[:max(1, min(int(limit), 50))],
                "source": "Acartia marine-mammal sightings (CC BY 4.0)",
                "basis": "Reports by observers and monitoring networks, not a census; most reports come from shore and ferries "
                         "by day. Places are rounded to about 1 km."}

    # ----------------------------------------------------------------- street photo
    def street_photo(self, place: str = "here", radius_m: float = 150) -> dict[str, Any]:
        where = self.explore._place(place)
        latitude, longitude = where["latitude"], where["longitude"]
        radius = max(20.0, min(float(radius_m), 1000.0))
        dlat, dlon = radius / 111320, radius / (111320 * max(.1, math.cos(math.radians(latitude))))
        url = PANORAMAX + "?" + urlencode({"bbox": f"{longitude - dlon:.6f},{latitude - dlat:.6f},{longitude + dlon:.6f},{latitude + dlat:.6f}",
                                            "limit": 40})
        data = self.explore._json(f"panoramax-{latitude:.4f},{longitude:.4f},{radius:.0f}", url, 3600, 8 * 1024 * 1024)
        best = None
        for feature in (data or {}).get("features") or []:
            point = (feature.get("geometry") or {}).get("coordinates") or []
            href = ((feature.get("assets") or {}).get("sd") or (feature.get("assets") or {}).get("thumb") or {}).get("href")
            if len(point) < 2 or not isinstance(href, str):
                continue
            host = urlsplit(href).hostname or ""
            if urlsplit(href).scheme != "https" or not host.endswith(PHOTO_HOSTS):
                continue  # the picture must come from a Panoramax storage host
            distance = distance_km(latitude, longitude, point[1], point[0]) * 1000
            if distance <= radius and (best is None or distance < best[0]):
                best = (distance, feature, href)
        if best is None:
            raise ValueError(f"No open street-level photo (Panoramax) within {radius:.0f} m of {where['name']}.")
        distance, feature, href = best
        content = bytes(self.explore.fetcher.fetch(href, 8 * 1024 * 1024, {"image/jpeg", "image/png", "image/webp"}).content)
        from PIL import Image  # Explore keeps PNGs; Panoramax sends JPEG
        with Image.open(io.BytesIO(content)) as picture:
            picture = picture.convert("RGB")
            if picture.width > 1280:
                picture = picture.resize((1280, round(picture.height * 1280 / picture.width)))
            buffer = io.BytesIO()
            picture.save(buffer, "PNG", optimize=True)
        path = self.explore._save(buffer.getvalue(), "street")
        properties = feature.get("properties") or {}
        # Some contributors' account names are e-mail addresses: credit them without repeating the address.
        producers = [("a Panoramax contributor" if "@" in item["name"] else item["name"]) for item in feature.get("providers") or []
                     if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()]
        heading = properties.get("view:azimuth")
        return {"intelligence": True, "explore": "street_photo", "place": where["name"], "path": str(path),
                "taken": properties.get("datetime"), "distance_m": round(distance), "latitude": feature["geometry"]["coordinates"][1],
                "longitude": feature["geometry"]["coordinates"][0],
                "facing": compass(float(heading)) if isinstance(heading, (int, float)) else None,
                "author": ", ".join(dict.fromkeys(producers)) or None, "license": properties.get("license"),
                "page": f"https://api.panoramax.xyz/#focus=pic&pic={feature.get('id')}",
                "source": "Panoramax open street-level imagery",
                "basis": "The nearest published street photo, as old as its date says; Panoramax blurs faces and number plates."}


GROUND_TOOLS = ("fire_perimeters", "whale_sightings", "street_photo")


def register_ground_tools(registry: Any, ground: Ground) -> None:
    def obj(properties):
        return {"type": "object", "properties": properties, "required": [], "additionalProperties": False}
    place = {"type": "string", "maxLength": 200}
    registry.register("fire_perimeters", "Mapped US wildfire and prescribed-burn perimeters near a place (NIFC): acres, "
                      "containment, cause, crews, how far the edge is, and whether the place is inside one.",
                      obj({"place": place, "radius_km": {"type": "number", "minimum": 5, "maximum": 3000},
                           "include_prescribed": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
                      "low", "public_web_read", ground.fire_perimeters)
    registry.register("whale_sightings", "Recent whale, orca and other marine-mammal sightings near a place in the Salish Sea "
                      "and Pacific Northwest (Acartia), by species and how long ago.",
                      obj({"place": place, "radius_km": {"type": "number", "minimum": 5, "maximum": 400},
                           "days": {"type": "integer", "minimum": 1, "maximum": 7}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
                      "low", "public_web_read", ground.whale_sightings)
    registry.register("street_photo", "The nearest open street-level photo of a place (Panoramax), saved as an image, with "
                      "its date, direction and licence.", obj({"place": place, "radius_m": {"type": "number", "minimum": 20, "maximum": 1000}}),
                      "low", "public_web_read", ground.street_photo)


def report_lines(value: dict[str, Any]) -> list[str]:
    kind = value["explore"]
    if kind == "perimeters":
        lines = [f"{value['count']} mapped fire perimeter{'s' if value['count'] != 1 else ''} within {value['radius_km']:g} km of "
                 f"{value['place']} (of {value['season_total']} current in the US)" + (" — the place is INSIDE one:" if value["inside"] else ":")]
        for fire in value["fires"][:10]:
            parts = [f"{fire['name']} ({fire['kind']}, {fire['state'] or '?'})",
                     "inside" if fire["inside"] else f"edge {fire['edge_km']} km {fire['direction'] or ''}".rstrip()]
            if fire.get("acres") is not None:
                parts.append(f"{fire['acres']:,.0f} acres")
            if fire.get("contained_pct") is not None:
                parts.append(f"{fire['contained_pct']:g}% contained")
            if fire.get("personnel"):
                parts.append(f"{fire['personnel']} personnel")
            if fire.get("cause"):
                parts.append(f"cause {str(fire['cause']).lower()}")
            if fire.get("perimeter_updated"):
                parts.append(f"perimeter {fire['perimeter_updated'][:10]}" + (" (stale)" if fire["perimeter_stale"] else ""))
            lines.append("• " + " · ".join(parts))
        return lines
    if kind == "whales":
        lines = [f"{value['count']} marine-mammal sighting{'s' if value['count'] != 1 else ''} within {value['radius_km']:g} km of "
                 f"{value['place']} in the last {value['days']} day{'s' if value['days'] > 1 else ''}"
                 + (": " + ", ".join(f"{name} {count}" for name, count in value["species"][:6]) if value["species"] else ".")]
        for item in value["sightings"][:10]:
            lines.append(f"• {item['species']}" + (f" ×{item['count']}" if item.get("count") and item["count"] > 1 else "")
                         + f" · {item['hours_ago']:g} h ago · {item['distance_km']} km {item['direction']}"
                         + ("" if item["trusted"] else " · unverified"))
        return lines
    if kind == "street_photo":
        return [f"Street photo {value['distance_m']} m from {value['place']}" + (f", facing {value['facing']}" if value.get("facing") else "")
                + (f", taken {str(value['taken'])[:10]}" if value.get("taken") else "") + ".",
                value["path"],
                "Photo: " + ", ".join(part for part in (value.get("author"), value.get("license")) if part) + f" · {value['page']}"]
    return []
