"""
nutrition.py — turns NetNutrition's FDA labels into numbers, and remembers them.

Every item row in a menu carries a `detailOid`, and POSTing that to
NutritionDetail/ShowItemNutritionLabel returns a rendered FDA nutrition label:
calories, the macro rows, ingredients, and an allergen line. That is one HTTP
request per row, and a full scrape sees ~1,300 rows, so fetching them fresh
every run is not an option.

The obvious fix — cache by detailOid — does not work. A detailOid identifies a
*serving of a recipe on one menu*, not the recipe: "Spinach" carries 16
different OIDs across one day's menus, and every new day mints a fresh set. A
detailOid cache would never get a hit and would grow without bound.

What is actually stable is the recipe. Fetching several OIDs of the same item
returns byte-identical nutrition, across meals and across dining halls — so the
cache is keyed on the item's name plus its serving size instead. That is 498
keys for 1,124 OIDs on a single day, and, far more importantly, those keys
recur day after day, so after the first couple of runs almost everything is a
hit. Serving size is part of the key because a few items are genuinely served
in two portions (Ranch Dressing at 1 oz. and at 2 oz.) with different numbers.

The cache doubles as the file the front-end loads, which also keeps the payload
small: one recipe is stored once no matter how many menus it appears on.
"""

import json
import os
import re

from bs4 import BeautifulSoup

from . import config

# "28g" -> 28.0, "1,430mg" -> 1430.0, "0.0mcg" -> 0.0, "NA" -> None.
# The comma group matters: without it "1,430mg" parsed as 1.0, and a
# high-sodium item got published as containing one milligram of salt.
AMOUNT_RE = re.compile(r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(mcg|mg|g|kcal)?", re.I)

# "less than 1g" / "<1g" — the FDA wording for a trace amount. Dropping the row
# loses real information; taking the bound overstates it. The bound means
# "somewhere in (0, 1)", so record its midpoint.
TRACE_RE = re.compile(r"^(?:less\s+than|<)\s*", re.I)

# "5 oz. portion (142g)" -> 142.0. The gram weight is what makes two servings
# comparable when one is "1 slice" and the other is "4 oz. portion".
GRAMS_RE = re.compile(r"\((\d+(?:\.\d+)?)\s*g\)")

# "1 Servings per container"
PER_CONTAINER_RE = re.compile(r"([\d.]+)\s+Servings?\s+per\s+container", re.I)

# "Include 5g Added Sugars" — the one row whose name and value share a span.
ADDED_SUGARS_RE = re.compile(r"Include\s+(.+?)\s+Added\s+Sugars", re.I)

# NetNutrition marks a value it could not fully compute — a recipe where some
# component has no data behind it — with this class. The number is still the
# best available answer, but it is a floor rather than a total, and saying so
# is the difference between an honest number and a wrong one.
INCOMPLETE_CLASS = "cbo_nn_LabelPrimaryDetailIncomplete"


def recipe_key(name: str, serving_size: str) -> str:
    """The cache key for one item. Must match the front-end's version exactly.

    Case- and whitespace-insensitive so a stray double space or a capitalisation
    change on Vanderbilt's side doesn't silently orphan a cached label.

    Uses .lower(), not .casefold(): the front-end rebuilds this key with
    JavaScript's toLowerCase(), and casefold() is the more aggressive of the
    two (it maps "ß" to "ss"). Matching lower() keeps both sides identical.
    """
    name = " ".join((name or "").split()).lower()
    serving = " ".join((serving_size or "").split()).lower()
    return f"{name}|{serving}"


def _amount(text):
    """Pull the leading number out of a label amount. None if there isn't one."""
    text = (text or "").replace("\xa0", " ").strip()
    if not text or text.upper() == "NA":
        return None
    trace = bool(TRACE_RE.match(text))
    match = AMOUNT_RE.match(TRACE_RE.sub("", text))
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value / 2 if trace else value


def _is_incomplete(el) -> bool:
    """True if the site flagged this value as computed from partial data."""
    return el is not None and INCOMPLETE_CLASS in (el.get("class") or [])


def parse_label(html: str):
    """Parse one ShowItemNutritionLabel fragment into a flat dict of numbers.

    Returns None when the fragment is empty or carries no nutrition at all,
    which happens for items Vanderbilt has not entered a recipe for.
    """
    if not html or not html.strip():
        return None
    soup = BeautifulSoup(html, "html.parser")
    label = soup.select_one("#nutritionLabel")
    if label is None:
        return None

    data = {}

    header = label.select_one(".cbo_nn_LabelHeader")
    if header:
        data["name"] = header.get_text(" ", strip=True)

    # Serving size sits in the right-hand half of its own row, with the words
    # "Serving Size" in the left half.
    for left in label.select(".inline-div-left"):
        if left.get_text(strip=True).rstrip(":") == "Serving Size":
            right = left.find_next_sibling(class_="inline-div-right")
            if right:
                serving = right.get_text(" ", strip=True).replace("\xa0", " ")
                data["serving"] = serving
                # "(142g)" — the only thing on the label that makes portions
                # comparable across "1 slice", "4 oz. portion" and "1 bagel".
                grams = GRAMS_RE.search(serving)
                if grams:
                    data["grams"] = float(grams.group(1))
            break

    # "1 Servings per container". Usually 1, but a shared item (a whole pizza,
    # a pitcher) says otherwise, and then every number on the label is per
    # serving rather than per the thing you actually took.
    per = PER_CONTAINER_RE.search(label.get_text(" ", strip=True))
    if per:
        try:
            data["servings_per_container"] = float(per.group(1))
        except ValueError:
            pass

    # Nutrients the site computed from incomplete recipe data. Collected as we
    # go and attached at the end.
    incomplete = []

    # Calories are the odd one out: the number lives in the sibling right-hand
    # div rather than in a second span of the same row.
    for span in label.select("span"):
        if span.get_text(strip=True) == "Calories":
            parent = span.find_parent(class_="inline-div-left")
            right = parent.find_next_sibling(class_="inline-div-right") if parent else None
            if right:
                data["calories"] = _amount(right.get_text(" ", strip=True))
            break

    # Every other nutrient is a two-span row: <span>Name</span><span> 28g</span>.
    # "Trans Fat" arrives as <span><i>Trans</i> Fat</span>, so read the whole
    # span rather than its first string. The vitamins and minerals in the
    # label's secondary table use the same shape, which is why adding them to
    # NUTRIENT_FIELDS was all it took to start capturing them.
    for left in label.select(".inline-div-left"):
        spans = left.find_all("span", recursive=False)

        # Added sugars is the one row that breaks the pattern: the site writes
        # name and value into a single span, "Include 5g Added Sugars".
        if "addedSugarRow" in (left.get("class") or []):
            match = ADDED_SUGARS_RE.search(left.get_text(" ", strip=True))
            if match and "added_sugars" not in data:
                data["added_sugars"] = _amount(match.group(1))
                if data["added_sugars"] is not None and _is_incomplete(left):
                    incomplete.append("added_sugars")
            continue

        if len(spans) < 2:
            continue
        name = spans[0].get_text(" ", strip=True).rstrip(":")
        key = config.NUTRIENT_FIELDS.get(name)
        if key and key not in data:
            data[key] = _amount(spans[-1].get_text(" ", strip=True))
            # A flagged row with an actual number is a partial total. A flagged
            # row reading "NA" is just absent, and is already recorded as null.
            if data[key] is not None and _is_incomplete(spans[-1]):
                incomplete.append(key)

    # Ingredients and the "Contains:" allergen line are plain text blocks that
    # follow their own bold headings.
    text = label.get_text("\n", strip=True)
    ing = re.search(
        r"Ingredients:\s*\n(.*?)(?:\nContains:|\n\*\s*The % Daily|\Z)", text, re.S
    )
    if ing:
        cleaned = " ".join(ing.group(1).split())
        if cleaned:
            data["ingredients"] = cleaned
    contains = re.search(r"Contains:\s*\n([^\n]+)", text)
    if contains:
        data["contains"] = " ".join(contains.group(1).split())

    # A label with a name but no calories and no macros is not worth storing.
    macro_keys = ("calories",) + tuple(config.NUTRIENT_FIELDS.values())
    if not any(k in data for k in macro_keys):
        return None
    if incomplete:
        data["incomplete"] = sorted(set(incomplete))
    return data


# -- the cache -------------------------------------------------------------


def load_cache(path: str = None) -> dict:
    """Read the committed nutrition cache. Missing or corrupt file -> empty."""
    path = path or config.NUTRITION_CACHE_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f).get("items", {})
    except (json.JSONDecodeError, OSError) as exc:
        # Never let a bad cache take the whole run down; worst case we refetch.
        print(f"  ! nutrition cache unreadable ({exc}) — starting empty")
        return {}
    if not isinstance(items, dict):
        return {}
    return items


def prune(cache: dict, today: str) -> dict:
    """Drop entries not seen on a menu in config.NUTRITION_TTL_DAYS.

    Vanderbilt retires recipes, and without this the committed file would only
    ever grow. Entries carry a `_seen` date stamped by backfill().
    """
    from datetime import date

    try:
        cutoff = date.fromisoformat(today).toordinal() - config.NUTRITION_TTL_DAYS
    except ValueError:
        return cache

    kept = {}
    for key, entry in cache.items():
        seen = (entry or {}).get("_seen")
        if not seen:
            # Pre-TTL entry, or one written before it was ever on a menu:
            # keep it once and let this run's stamp decide next time.
            kept[key] = entry
            continue
        try:
            if date.fromisoformat(seen).toordinal() >= cutoff:
                kept[key] = entry
        except ValueError:
            kept[key] = entry

    dropped = len(cache) - len(kept)
    if dropped:
        print(f"Nutrition: pruned {dropped} recipes unseen for "
              f"{config.NUTRITION_TTL_DAYS} days")
    return kept


def save_cache(items: dict, generated_at: str, path: str = None) -> None:
    """Write the cache atomically, so a crash can't truncate what's served."""
    path = path or config.NUTRITION_CACHE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "generated_at": generated_at,
        "item_count": len(items),
        # Sorted so the committed file diffs cleanly from run to run.
        "items": dict(sorted(items.items())),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _current(entry) -> bool:
    """True if a cached entry was written by the parser we are running now.

    Without this a parser improvement only ever reaches recipes Vanderbilt
    happens to add afterwards: everything already cached keeps being a hit and
    is never re-read. Stale entries go back into the refetch queue and drain
    through the normal per-run budget over a couple of runs.
    """
    return isinstance(entry, dict) and entry.get("_v") == config.NUTRITION_SCHEMA


def backfill(nn, menus: list, cache: dict, today: str) -> dict:
    """Fetch a label for every recipe on `menus` we don't already have.

    `nn` is an open fetcher.NetNutrition session. Mutates and returns `cache`.
    Stops after config.NUTRITION_BUDGET new labels so a menu-wide rollover
    can't stretch one Action run past its timeout — the rest lands next run.
    """
    # For each recipe key, any one (detailOid, menuOid) pair will do: every OID
    # of a given recipe returns the same label.
    wanted = {}
    for menu in menus:
        for cat in menu["categories"]:
            for item in cat["items"]:
                oid = item.get("detail_oid")
                if oid is None:
                    continue
                key = recipe_key(item["name"], item["serving_size"])
                if key in cache and _current(cache[key]):
                    # Still on a menu today, so keep it alive through pruning.
                    if isinstance(cache[key], dict):
                        cache[key]["_seen"] = today
                    continue
                wanted.setdefault(key, (oid, menu["menu_oid"]))

    if not wanted:
        print(f"Nutrition: cache covers all {len(cache)} recipes on today's menus.")
        return cache

    todo = list(wanted.items())[: config.NUTRITION_BUDGET]
    print(
        f"Nutrition: {len(cache)} cached, {len(wanted)} new recipes "
        f"({len(todo)} this run, budget {config.NUTRITION_BUDGET})"
    )

    added = skipped = failed = 0
    for key, (detail_oid, menu_oid) in todo:
        try:
            html = nn.fetch_nutrition_label(detail_oid, menu_oid)
            parsed = parse_label(html)
        except Exception as exc:
            print(f"  ! label for {key!r}: {exc}")
            failed += 1
            continue
        if parsed is None:
            # Remember the miss too, or every future run retries this recipe
            # forever. No macro keys reads as "asked, nothing published".
            cache[key] = {"_seen": today, "_v": config.NUTRITION_SCHEMA}
            skipped += 1
            continue
        parsed["_seen"] = today
        parsed["_v"] = config.NUTRITION_SCHEMA
        cache[key] = parsed
        added += 1

    left = len(wanted) - len(todo)
    print(
        f"Nutrition: +{added} labels, {skipped} unpublished, {failed} failed"
        + (f", {left} deferred to the next run" if left else "")
    )
    return cache


def has_macros(entry) -> bool:
    """True if a cache entry actually carries numbers (not just a _seen stamp)."""
    if not isinstance(entry, dict):
        return False
    return entry.get("calories") is not None or any(
        entry.get(k) is not None for k in config.NUTRIENT_FIELDS.values()
    )
