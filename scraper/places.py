"""
places.py — street addresses, websites and opening hours for the Taste of
Nashville partners, from OpenStreetMap.

Vanderbilt's off-campus dining page is a bare list of names: no addresses, no
hours, no links, no map data of any kind (the only structured block on the page
is a CMS `NewsArticle`). So anything richer has to come from elsewhere.

OpenStreetMap via the Overpass API is the only source that fits this project's
constraints — free, no API key, no billing account, no secret to leak from a
public repo. The tradeoff is coverage: OSM knows roughly two thirds of these
places, and only about a quarter of them carry `opening_hours`. That is a real
limitation, not a bug, and the front-end says so per restaurant rather than
implying a blank means "closed" or inventing a plausible schedule.

Matching names is the delicate part. OSM spells things differently than
Vanderbilt does ("Hyderabad House" vs "Nawabi Hyderabad House", "Papa John's"
vs "Papa John's Pizza"), and chains have several Nashville branches, so a naive
name match can happily return a franchise five miles away. See `match_place`.
"""

import json
import math
import os
import re
import time

import requests

from . import config

# Hand-checked weekly hours from each restaurant's Google Maps listing. Google
# is the freshest source there is for this (owners maintain it, students trust
# it), but its terms don't allow automated scraping — so this file is collected
# manually in a browser and committed, and the pipeline overlays it on top of
# whatever OSM knows. See the file's own _comment for the collection notes.
GOOGLE_HOURS_PATH = os.path.join(os.path.dirname(__file__), "data", "google_hours.json")


# Vanderbilt's campus, used to break ties between branches of a chain.
CAMPUS_LAT, CAMPUS_LON = 36.1447, -86.8027

# Words that carry no identifying weight when comparing two names.
NOISE = re.compile(
    r"\b(the|restaurant|restaurants|nashville|co|company|inc|llc|"
    r"midtown|hillsboro|village|west|end)\b"
)


def normalize(name: str) -> str:
    """Reduce a business name to a comparable core: 'Jeni's Splendid' -> jenissplendid."""
    s = (name or "").lower().replace("’", "'").replace("‘", "'")
    s = NOISE.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km. Used only to rank candidates, so exactness
    beyond a few metres does not matter."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_pois() -> list:
    """Query Overpass for every eating/drinking POI around campus.

    One request per run, for all 36 restaurants at once — asking per-restaurant
    would be 36 queries against a donated, shared service.
    """
    south, west, north, east = config.PLACES_BBOX
    query = f"""
[out:json][timeout:{config.PLACES_TIMEOUT}];
(
  node["amenity"~"restaurant|cafe|fast_food|ice_cream|bar|pub|juice_bar"]({south},{west},{north},{east});
  way["amenity"~"restaurant|cafe|fast_food|ice_cream|bar|pub|juice_bar"]({south},{west},{north},{east});
);
out center tags;
"""
    resp = requests.post(
        config.OVERPASS_URL,
        data={"data": query},
        # Overpass asks that clients identify themselves; an anonymous flood is
        # what gets IP ranges blocked from a free, donation-funded service.
        headers={"User-Agent": config.OVERPASS_UA},
        timeout=config.PLACES_TIMEOUT + 15,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    pois = []
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        # Nodes carry lat/lon directly; ways come back with a `center`.
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        pois.append({"name": name, "lat": lat, "lon": lon, "tags": tags,
                     "id": f"{el.get('type')}/{el.get('id')}"})
    return pois


def match_place(name: str, pois: list, neighborhood: str = None):
    """Find the POI that is most likely this restaurant, or None.

    Three passes, strictest first. Every pass anchors on the neighborhood
    Vanderbilt listed the restaurant in and rejects anything outside it, which
    is what stops a chain's other branch from being published as this one.
    """
    target = normalize(name)
    if len(target) < 3:
        return None

    center = config.NEIGHBORHOOD_CENTERS.get(neighborhood)
    limit = config.PLACES_MAX_KM if center else config.PLACES_FALLBACK_KM
    anchor = center or (CAMPUS_LAT, CAMPUS_LON)

    def nearest(cands):
        best = min(cands, key=lambda p: haversine_km(anchor[0], anchor[1], p["lat"], p["lon"]))
        # Right name, wrong branch: drop it rather than publish a bad address.
        if haversine_km(anchor[0], anchor[1], best["lat"], best["lon"]) > limit:
            return None
        return best

    exact = [p for p in pois if p["_norm"] == target]
    if exact:
        hit = nearest(exact)
        if hit:
            return hit

    # "Papa John's Pizza" vs OSM's "Papa John's"; "Nawabi Hyderabad House" vs
    # "Hyderabad House". Require a decent length so "Sitar" can't swallow
    # every name containing those five letters.
    contains = [
        p for p in pois
        if len(p["_norm"]) >= 5 and len(target) >= 5
        and (p["_norm"] in target or target in p["_norm"])
    ]
    if contains:
        hit = nearest(contains)
        if hit:
            return hit

    # Last resort: close spelling. Kept tight — a loose ratio here is how you
    # end up telling someone the wrong restaurant's hours.
    scored = []
    for p in pois:
        ratio = _ratio(target, p["_norm"])
        if ratio >= config.PLACES_FUZZ:
            scored.append((ratio, p))
    if not scored:
        return None
    best = max(s[0] for s in scored)
    return nearest([p for r, p in scored if r == best])


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def enrich(restaurant_list: list) -> list:
    """Attach OSM detail to each restaurant. Returns a new list.

    Every restaurant always gets a `maps` link, matched or not — that link is
    built from the name and needs no API, and it is the one thing that reliably
    answers "where is this and is it open right now".
    """
    try:
        pois = fetch_pois()
    except Exception as exc:
        print(f"  ! OpenStreetMap lookup failed ({exc})")
        pois = []

    for p in pois:
        p["_norm"] = normalize(p["name"])
    print(f"OpenStreetMap: {len(pois)} nearby POIs to match against")

    out = []
    matched = with_hours = 0
    for entry in restaurant_list:
        row = dict(entry)
        row["maps"] = maps_link(entry["name"], None)

        place = (match_place(entry["name"], pois, entry.get("neighborhood"))
                 if pois else None)
        if place:
            tags = place["tags"]
            addr = street_address(tags)
            row["osm_id"] = place["id"]
            row["osm_name"] = place["name"]
            row["lat"] = round(place["lat"], 6)
            row["lon"] = round(place["lon"], 6)
            if addr:
                row["address"] = addr
                row["maps"] = maps_link(entry["name"], addr)
            for tag, key in (("opening_hours", "hours"), ("website", "website"),
                             ("phone", "phone"), ("cuisine", "cuisine")):
                if tags.get(tag):
                    row[key] = tags[tag]
            matched += 1
            if row.get("hours"):
                with_hours += 1
        out.append(row)

    print(
        f"OpenStreetMap: matched {matched}/{len(restaurant_list)}, "
        f"{with_hours} with opening hours"
    )
    return out


def apply_google_hours(restaurant_list: list) -> list:
    """Overlay the hand-checked Google Maps hours on top of OSM's. New list.

    Runs on every pipeline pass (including ones that reuse cached Overpass
    detail), so the checked-in file is always what wins. Google's data beats
    OSM's on both coverage and freshness, so where an entry exists it replaces
    the OSM hours and fills in a missing address; where it doesn't, whatever
    OSM said stands.
    """
    try:
        with open(GOOGLE_HOURS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ! Google hours file unreadable ({exc}) — keeping OSM hours")
        return restaurant_list

    overrides = {normalize(e["name"]): e for e in data.get("restaurants", [])}
    checked = data.get("checked")

    out = []
    with_hours = 0
    for entry in restaurant_list:
        row = dict(entry)
        ov = overrides.get(normalize(entry["name"]))
        if ov:
            if ov.get("hours"):
                row["hours"] = ov["hours"]
                row["hours_source"] = "Google Maps"
                row["hours_checked"] = checked
                with_hours += 1
            if ov.get("address") and not row.get("address"):
                row["address"] = ov["address"]
                row["maps"] = maps_link(entry["name"], ov["address"])
            if ov.get("permanently_closed"):
                row["permanently_closed"] = True
            if ov.get("note"):
                row["note"] = ov["note"]
        out.append(row)

    print(f"Google Maps hours: {with_hours}/{len(restaurant_list)} applied (checked {checked})")
    return out


def street_address(tags: dict):
    """Assemble 'housenumber street, city' from OSM's split address tags."""
    number = tags.get("addr:housenumber", "").strip()
    street = tags.get("addr:street", "").strip()
    city = tags.get("addr:city", "").strip()
    line = " ".join(x for x in (number, street) if x)
    if not line:
        return None
    return f"{line}, {city}" if city else line


def maps_link(name: str, address) -> str:
    """A plain Google Maps search URL — no API, no key, always works.

    Deliberately a *search* rather than a pinned coordinate: it lands the reader
    on Google's own listing, which carries live hours and reviews, and so stays
    right even where OSM is thin.
    """
    from urllib.parse import quote_plus

    query = f"{name}, {address}" if address else f"{name}, Nashville, TN"
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)
