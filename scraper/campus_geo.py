"""
campus_geo.py — where things are, for the schedule-aware "what's nearby" view.

Two things need coordinates that NetNutrition and the dining site never give
you: each dining hall, and the classroom buildings a student picks when they
enter their schedule. Same constraint as places.py — free, no API key, no
billing account — so this is OpenStreetMap via Overpass again: one query for
every named building inside the campus bbox, then dining halls are matched
against that list by name with the same normalize/fuzzy approach places.py
uses for restaurants.
"""

import re

import requests

from . import config
from .places import haversine_km, normalize

# Matches a dining hall's own fuzzy fallback: tight on purpose, because a wrong
# match here points someone at the wrong building, not just the wrong address.
_BUILDING_FUZZ = 0.82


def fetch_buildings() -> list:
    """Every named building/POI inside the campus bbox. One Overpass query.

    `building=*` catches classroom buildings and residence halls; the dining
    halls themselves are also `amenity=*` points in many cases, so both are
    pulled and de-duplicated by name, keeping whichever has a tighter geometry
    (a node over a way's centroid) when both exist.
    """
    south, west, north, east = config.CAMPUS_BBOX
    query = f"""
[out:json][timeout:{config.PLACES_TIMEOUT}];
(
  way["building"]["name"]({south},{west},{north},{east});
  node["amenity"]["name"]({south},{west},{north},{east});
);
out center tags;
"""
    resp = requests.post(
        config.OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": config.OVERPASS_UA},
        timeout=config.PLACES_TIMEOUT + 15,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    found = {}
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        key = normalize(name)
        if not key:
            continue
        # A node (exact point) beats a way's centroid for the same name.
        if key in found and found[key]["_is_node"] and el.get("type") != "node":
            continue
        found[key] = {
            "name": name, "lat": lat, "lon": lon,
            "_norm": key, "_is_node": el.get("type") == "node",
        }

    return list(found.values())


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def match_building(name: str, buildings: list):
    """Find the building whose name best matches `name`, anchored to campus."""
    target = normalize(name)
    if len(target) < 3 or not buildings:
        return None

    def nearest(cands):
        best = min(
            cands,
            key=lambda b: haversine_km(config.CAMPUS_LAT, config.CAMPUS_LON, b["lat"], b["lon"]),
        )
        if haversine_km(config.CAMPUS_LAT, config.CAMPUS_LON, best["lat"], best["lon"]) > config.CAMPUS_MAX_KM:
            return None
        return best

    exact = [b for b in buildings if b["_norm"] == target]
    if exact:
        return nearest(exact)

    contains = [
        b for b in buildings
        if len(b["_norm"]) >= 4 and len(target) >= 4
        and (b["_norm"] in target or target in b["_norm"])
    ]
    if contains:
        return nearest(contains)

    scored = [(( _ratio(target, b["_norm"])), b) for b in buildings]
    scored = [(r, b) for r, b in scored if r >= _BUILDING_FUZZ]
    if not scored:
        return None
    best = max(r for r, _ in scored)
    return nearest([b for r, b in scored if r == best])


def locate_halls(halls: list, buildings: list) -> list:
    """Attach lat/lon to each dining hall dict it can be matched to. New list.

    Best-effort: a hall OSM doesn't know about (or know under a very different
    name) is simply published without coordinates, and the front-end treats
    that as "distance unknown" rather than guessing. A hand-checked override in
    config.DINING_HALL_COORDS always wins — OSM's point for a dining hall is
    sometimes the building entrance rather than the servery, and a few halls
    (food-truck stops, the Munchie Marts) aren't named buildings at all.
    """
    out = []
    matched = 0
    for hall in halls:
        row = dict(hall)
        override = config.DINING_HALL_COORDS.get(hall["name"])
        if override:
            row["lat"], row["lon"] = override
            matched += 1
        else:
            place = match_building(hall["name"], buildings) if buildings else None
            if place:
                row["lat"] = round(place["lat"], 6)
                row["lon"] = round(place["lon"], 6)
                matched += 1
        out.append(row)

    print(f"Campus geo: {matched}/{len(halls)} dining halls located")
    return out


def locate_buildings(names: list, buildings: list) -> list:
    """Resolve a curated list of building names to {name, lat, lon}, for the
    schedule picker. Skips anything OSM doesn't know rather than guessing."""
    out = []
    for name in names:
        override = config.DINING_HALL_COORDS.get(name)
        if override:
            out.append({"name": name, "lat": override[0], "lon": override[1]})
            continue
        place = match_building(name, buildings)
        if place:
            out.append({"name": name, "lat": round(place["lat"], 6), "lon": round(place["lon"], 6)})
    return out
