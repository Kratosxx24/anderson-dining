# Anderson Dining

Every Vanderbilt campus dining menu — with calories and macros — scraped every
three hours into one filterable page, plus the Taste of Nashville partner list
and a browser-local intake tracker. Same free-forever architecture as
`anderson-wire`:

```
GitHub Actions cron  ->  main.py  ->  docs/menu.json      ->  GitHub Pages
                                  ->  docs/nutrition.json
```

No servers, no database, no API keys, no accounts, $0/month.

## What it does

`scraper/fetcher.py` walks Vanderbilt's NetNutrition site and pulls today's and
tomorrow's menus for every dining facility that publishes one — dining hall,
meal period, course/station, item name, serving size, and the dietary and
allergen tags Vanderbilt attaches to each item (Vegan, Halal, Gluten, Tree
Nut, …).

`scraper/nutrition.py` then fills in the numbers: calories, fat, saturated fat, trans
fat, cholesterol, sodium, carbs, fiber, sugars, protein, plus the full
ingredient list and allergen line for each recipe.

`scraper/restaurants.py` scrapes the Taste of Nashville partner list — the off-campus
restaurants where Meal Money works — from Vanderbilt's dining site.

`main.py` writes `docs/menu.json` and `docs/nutrition.json`. `docs/index.html`
reads both and renders three views: **Menus** (filter by day, meal, traits,
allergens, text, and now sort by protein or calories), **Taste of Nashville**,
and **Tracker**.

## How the menu scrape works

NetNutrition is **not** a REST API. It's a session-based ASP.NET MVC app whose
AJAX endpoints return rendered HTML fragments inside a JSON envelope:

```json
{"success": true, "panels": [{"id": "menuPanel", "html": "<section>…"}]}
```

So the scraper holds a `requests.Session` (for `ASP.NET_SessionId` plus the AWS
ALB cookie) and makes four kinds of call:

| Step | Call | Gives you |
| --- | --- | --- |
| 1 | `GET /nn-prod/vucampusdining` | session cookies + the dining-hall list |
| 2 | `POST /Unit/SelectUnitFromUnitsList` `{unitOid}` | that hall's date × meal grid |
| 3 | `POST /Menu/SelectMenu` `{menuOid}` | the item table for one meal |
| 4 | `POST /NutritionDetail/ShowItemNutritionLabel` `{detailOid, menuOid}` | one item's FDA label |

The IDs never appear in a URL. Unit OIDs live in
`onclick="…unitsSelectUnit(11)"`, menu OIDs in
`onclick="…menuListSelectMenu(9125337)"`, and item OIDs in
`onclick="…getItemNutritionLabelOnClick(event,282517631)"` — all regexed out of
the HTML. `X-Requested-With: XMLHttpRequest` is required for steps 2 and 3 —
without it the endpoints return a full page instead of the JSON envelope. Step
4 is the odd one out: it answers with a bare HTML fragment, no envelope.
Selecting a unit resets the session's menu context, so step 2 is repeated
before each hall's step-3 batch.

Inside `itemPanel` the table is flat, not nested: a `tr.cbo_nn_itemGroupRow`
announces a station ("Pizza") and every following `tr.cbo_nn_itemPrimaryRow`
belongs to it until the next group row. Dietary tags come from the `alt` text
of the icon images that follow the item name.

## How nutrition is cached (and why it's keyed the way it is)

Step 4 is one HTTP request **per item row**, and a run sees ~1,300 rows. That
cannot happen every time, so results are cached in `docs/nutrition.json`, which
is committed back to the repo and is also the file the browser loads.

The obvious cache key — `detailOid` — does **not** work. A `detailOid`
identifies *a serving of a recipe on one menu*, not the recipe itself:
"Spinach" carries 16 different OIDs across a single day's menus, and every new
day mints a fresh set. A detailOid-keyed cache would never get a hit and would
grow without bound.

What is stable is the recipe. Fetching several OIDs of the same item returns
byte-identical nutrition, across meal periods and across dining halls. So the
key is **item name + serving size**, normalized (whitespace collapsed,
lowercased). That's 498 keys for 1,124 OIDs on one day — and, far more
importantly, those keys recur day after day, so after the first couple of runs
almost every lookup is a hit. Serving size is part of the key because a handful
of items genuinely ship in two portions (Ranch Dressing at 1 oz. and at 2 oz.)
with different numbers behind them.

Two consequences worth knowing:

- **Cold start is gradual.** `NUTRITION_BUDGET` (default 400) caps how many new
  labels one run will fetch, so a menu-wide rollover can't stretch an Action run
  past its timeout. Whatever is skipped is picked up by the next run; with eight
  runs a day the cache fills within a day and stays full.
- **`recipe_key()` in `scraper/nutrition.py` and `nkey()` in `index.html` must agree
  exactly.** They are the join between the two JSON files. Both use plain
  lowercasing — not Python's `casefold()`, which is more aggressive.

Entries carry a `_seen` date and are pruned after `NUTRITION_TTL_DAYS` (120)
without appearing on a menu, so the committed file stays bounded as Vanderbilt
retires dishes. An entry with a `_seen` stamp and no numbers means "asked,
nothing published" — that's cached too, or every run would retry it forever.

## Taste of Nashville

These are off-campus restaurants, so they are not in NetNutrition at all — no
menus, no items, no nutrition. Vanderbilt publishes them as a plain
neighborhood-grouped list, which `scraper/restaurants.py` scrapes: inside the "Taste of
Nashville" section, a `<p>` ending in a colon names a neighborhood and the
`<li>` elements after it are that neighborhood's restaurants.

If the scrape returns fewer than five entries (a fetch failure, or a page
redesign), `main.py` keeps the previous run's list rather than blanking out a
working section of the site.

## The tracker

`docs/index.html` has a third view that logs what you eat. Hitting **+** on any
menu item records it with the macros it had at that moment; there's also a
manual-entry form for Taste of Nashville meals and anything else off-menu. It
shows today's totals against editable goals, today's log, a by-hour histogram of
when you actually eat, and calories per day over the last two weeks.

All of it lives in `localStorage` under `dining-log-v1`. There is no server and
no account — nothing is uploaded, and clearing browser data clears the log.
Logged entries store their own numbers rather than a reference, so a menu change
months later never rewrites your history.

## Output shape

`docs/menu.json`:

```jsonc
{
  "generated_at": "2026-09-01T20:10:00+00:00",
  "campus_date": "2026-09-01",          // "today" in Central time, not UTC
  "menu_count": 44,
  "item_count": 1266,
  "nutrition": {                        // how honest the front-end can be
    "cached_recipes": 498, "with_macros": 486,
    "recipes_covered": 486, "recipes_total": 498
  },
  "tags": ["Vegetarian", "Vegan", "…"], // every tag this run actually saw
  "halls": [{ "id": 1, "name": "Rand Dining Center", "status": "Closed", "menu_count": 4 }],
  "restaurants": [{ "neighborhood": "Hillsboro Village", "name": "Biscuit Love" }],
  "menus": [{
    "hall": "Rand Dining Center",
    "date": "2026-09-01",
    "meal": "Lunch",
    "categories": [{
      "name": "Pizza",
      "items": [{ "name": "Cheese Pizza", "serving_size": "2 slices",
                  "tags": ["Dairy", "Gluten", "Vegetarian"] }]
    }]
  }]
}
```

`docs/nutrition.json`, keyed by `"name|serving size"`:

```jsonc
{
  "generated_at": "2026-09-01T20:10:00+00:00",
  "item_count": 498,
  "items": {
    "cheese pizza|2 slices": {
      "name": "Cheese Pizza", "serving": "2 slices (268g)",
      "calories": 630.0, "fat": 28.0, "sat_fat": 14.0, "trans_fat": 0.0,
      "cholesterol": 60.0, "sodium": 1430.0, "carbs": 69.0, "fiber": 4.0,
      "sugars": 7.0, "protein": 31.0,
      "ingredients": "Dough Pizza Crust White (ENRICHED FLOUR …",
      "contains": "Dairy, Gluten",
      "_seen": "2026-09-01"
    }
  }
}
```

## Layout

```
main.py               entry point — wires the pipeline together, writes docs/
scraper/
  config.py           every tunable knob, and nothing else
  fetcher.py          the NetNutrition session: halls, menus, item rows
  nutrition.py        FDA labels, their parsing, and the recipe-keyed cache
  restaurants.py      the Taste of Nashville partner list
docs/                 the published site (GitHub Pages serves this folder)
  index.html          the whole front-end: three views, no build step, no deps
  menu.json           written each run
  nutrition.json      the recipe cache; also what the browser loads
```

## Running it

```bash
pip install -r requirements.txt
python main.py
```

A run with a warm cache is ~120 requests over a couple of minutes. A cold cache
adds up to `NUTRITION_BUDGET` more requests (~4 minutes at the default 400).
`tzdata` is a real dependency, not an optional one: `zoneinfo` needs the IANA
database to resolve `America/Chicago`, and Windows and slim containers don't
ship it.

## Configuration

Everything lives in `scraper/config.py`:

| Knob | Does |
| --- | --- |
| `DAYS_AHEAD` | how far forward to capture (each extra day is ~100 more requests) |
| `REQUEST_DELAY` | politeness throttle between requests |
| `NUTRITION_BUDGET` | max new labels fetched per run |
| `NUTRITION_TTL_DAYS` | how long an unseen recipe stays cached |
| `NUTRIENT_FIELDS` | which label rows get lifted, and their JSON keys |
| `RESTAURANTS_URL` | the Taste of Nashville page |
| `MEAL_ORDER`, `KNOWN_TAGS` | display ordering only; the scraper records whatever the site sends |

## Deploying

1. Push to GitHub.
2. Settings → Pages → source **Deploy from a branch**, branch `main`, folder `/docs`.
3. Settings → Actions → General → Workflow permissions → **Read and write**.

The workflow runs every three hours and commits `docs/menu.json` and
`docs/nutrition.json` back to the repo. The menus themselves only change daily;
the reason to run more often is that each run also backfills a slice of the
nutrition cache.

## Caveats

Tags and nutrition are whatever Vanderbilt publishes; **confirm allergens with
dining staff**. Items Vanderbilt has no recipe for show "no label" rather than a
guess — the footer says what fraction of the day's items have real numbers
behind them. Facilities with no published menu for the captured window (Wasabi,
the Suzie's locations, the food truck) simply don't appear. If a run suddenly
returns zero menus, CBORD changed their markup — check the selectors in
`scraper/fetcher.py`.
