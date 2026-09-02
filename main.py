"""
main.py — the orchestrator. Run this and it does the whole pipeline:

  scrape NetNutrition -> backfill nutrition labels -> scrape Taste of Nashville
  -> write docs/menu.json + docs/nutrition.json

The GitHub Action runs this on a schedule; you can also run it locally with
`python main.py`. No API keys, no paid services.

Nutrition is written to its own file rather than inlined into each menu item:
one recipe shows up on a dozen menus, so keying it by recipe stores it once
instead of a dozen times, and lets the browser cache the slow-changing
nutrition separately from the menus that change daily. The front-end joins the
two by rebuilding the same key from an item's name and serving size.
"""

import json
import os
from datetime import datetime, timezone

import config
import fetcher
import nutrition
import restaurants

OUTPUT_PATH = os.path.join("docs", "menu.json")


def write_json(path: str, payload: dict) -> None:
    """Write JSON atomically, so a crash mid-write can't serve a truncated file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def scrape_restaurants(previous: list) -> list:
    """Fetch the Taste of Nashville list, falling back to what we had.

    The partner list changes maybe twice a year, so a fetch failure or a page
    redesign should never blank out a working section of the site. If the
    scrape comes back empty or implausibly short, we keep the last good list.
    """
    try:
        found = restaurants.fetch_restaurants()
    except Exception as exc:
        print(f"  ! Taste of Nashville scrape failed ({exc})")
        found = []

    if len(found) < 5 and previous:
        print(
            f"  ! Taste of Nashville returned {len(found)} entries — keeping the "
            f"previous {len(previous)}"
        )
        return previous

    print(f"Taste of Nashville: {len(found)} restaurants")
    return found


def load_previous() -> dict:
    """Read the last menu.json, for the fallbacks above. Missing file -> {}."""
    if not os.path.exists(OUTPUT_PATH):
        return {}
    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    started = datetime.now(timezone.utc)
    print("=" * 60)
    print(f"ANDERSON DINING RUN — {started.isoformat()}")
    print("=" * 60)

    previous = load_previous()

    data = fetcher.fetch_all()
    menus = data["menus"]

    # -- nutrition ---------------------------------------------------------
    campus_date = fetcher.campus_today().isoformat()
    cache = nutrition.load_cache()
    cache = nutrition.backfill(data["session"], menus, cache, campus_date)
    cache = nutrition.prune(cache, campus_date)
    generated_at = datetime.now(timezone.utc).isoformat()
    nutrition.save_cache(cache, generated_at)

    # An entry with only a _seen stamp means "asked, nothing published" — a
    # real answer, but not coverage, so don't count it as such.
    with_macros = sum(1 for v in cache.values() if nutrition.has_macros(v))
    recipes = {
        nutrition.recipe_key(i["name"], i["serving_size"])
        for m in menus
        for c in m["categories"]
        for i in c["items"]
    }
    covered = sum(1 for key in recipes if nutrition.has_macros(cache.get(key)))

    # detail_oid got us the labels; it means nothing to the front-end (it is
    # per-serving and churns daily) so it does not belong in the published file.
    for menu in menus:
        for cat in menu["categories"]:
            for item in cat["items"]:
                item.pop("detail_oid", None)

    # -- taste of nashville ------------------------------------------------
    partners = scrape_restaurants(previous.get("restaurants", []))

    # -- assemble ----------------------------------------------------------
    # Only surface tags the scrape actually saw, but keep KNOWN_TAGS' order so
    # the filter chips don't reshuffle between runs.
    seen = {tag for m in menus for c in m["categories"] for i in c["items"] for tag in i["tags"]}
    tags = [t for t in config.KNOWN_TAGS if t in seen]
    tags += sorted(seen - set(tags))

    payload = {
        "generated_at": generated_at,
        "campus_date": campus_date,
        "source": config.BASE_URL,
        "restaurants_source": config.RESTAURANTS_URL,
        "menu_count": len(menus),
        "item_count": sum(m["item_count"] for m in menus),
        "nutrition": {
            # What the front-end needs to caveat its own numbers honestly.
            "cached_recipes": len(cache),
            "with_macros": with_macros,
            "recipes_covered": covered,
            "recipes_total": len(recipes),
        },
        "tags": tags,
        "halls": data["halls"],
        "restaurants": partners,
        "menus": menus,
    }

    write_json(OUTPUT_PATH, payload)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(
        f"\nWrote {payload['menu_count']} menus "
        f"({payload['item_count']} items) to {OUTPUT_PATH}"
    )
    print(
        f"Nutrition covers {covered}/{len(recipes)} of today's recipes "
        f"({with_macros} recipes cached in {config.NUTRITION_CACHE_PATH})"
    )
    print(f"Done in {elapsed:.0f}s.")


if __name__ == "__main__":
    main()
