# Anderson Dining

Every Vanderbilt campus dining menu — with calories and macros — scraped every
three hours into one filterable page, plus the Taste of Nashville partner list
with addresses and opening hours, and a browser-local intake tracker. Same free-forever architecture as
`anderson-wire`:

```
GitHub Actions cron  ->  main.py  ->  docs/menu.json      ->  GitHub Pages
                                  ->  docs/nutrition.json
```

No servers, no database, no API keys, no accounts, $0/month. Every external
source used here (NetNutrition, the dining site, Overpass) is public and
keyless, so nothing secret ever has to live in this repo.

## What it does

`scraper/fetcher.py` walks Vanderbilt's NetNutrition site and pulls today's and
tomorrow's menus for every dining facility that publishes one — dining hall,
meal period, course/station, item name, serving size, and the dietary and
allergen tags Vanderbilt attaches to each item (Vegan, Halal, Gluten, Tree
Nut, …).

`scraper/nutrition.py` then fills in the numbers: calories, fat, saturated fat,
trans fat, cholesterol, sodium, carbs, fiber, sugars, added sugars, protein,
calcium, iron, potassium, vitamin C and vitamin D, plus the serving's gram
weight, the full ingredient list, and the allergen line for each recipe. Where
Vanderbilt computed a value from a recipe with gaps in it, that value is
flagged as a minimum rather than published as a total.

`scraper/hours.py` reads each venue's week of service hours from the pop-up
behind NetNutrition's Open/Closed pill. Those hours do two jobs, both covered
under [Meal periods and all-day menus](#meal-periods-and-all-day-menus).

`scraper/restaurants.py` scrapes the Taste of Nashville partner list — the off-campus
restaurants where Meal Money works — from Vanderbilt's dining site, and
`scraper/places.py` looks each one up in OpenStreetMap for a street address,
website, phone and opening hours.

`main.py` writes `docs/menu.json` and `docs/nutrition.json`. `docs/index.html`
reads both and renders three views: **Menus** (filter by day, meal, open-now,
traits, allergens and text; sort by protein or calories), **Taste of
Nashville**, and **Tracker**.

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
| 5 | `POST /Unit/GetHoursOfOperationMarkup` `{unitOid}` | that venue's week of service hours |

The IDs never appear in a URL. Unit OIDs live in
`onclick="…unitsSelectUnit(11)"`, menu OIDs in
`onclick="…menuListSelectMenu(9125337)"`, and item OIDs in
`onclick="…getItemNutritionLabelOnClick(event,282517631)"` — all regexed out of
the HTML. `X-Requested-With: XMLHttpRequest` is required for steps 2 and 3 —
without it the endpoints return a full page instead of the JSON envelope. Step
4 and 5 are the odd ones out: they answer with a bare HTML fragment, no
envelope.
Selecting a unit resets the session's menu context, so step 2 is repeated
before each hall's step-3 batch.

Inside `itemPanel` the table is flat, not nested: a `tr.cbo_nn_itemGroupRow`
announces a station ("Pizza") and every following `tr.cbo_nn_itemPrimaryRow`
belongs to it until the next group row. Dietary tags come from the `alt` text
of the icon images that follow the item name.

## Meal periods and all-day menus

Most halls publish one menu per meal period, so filtering by "Lunch" is a
straight match. Several venues don't. The Pub at Overcup Oak, all three Munchie
Marts and Local Java publish a **single menu called "Daily Offerings"** with no
meal period attached at all — it's the same food from open to close.

An exact `meal === "Lunch"` match therefore hid them completely, even at 12:30
on a Tuesday with the Pub open and serving. That's what step 5 fixes. With the
week's hours in hand, an all-day menu is shown under any meal period whose
window overlaps the venue's hours for that date:

```python
MEAL_WINDOWS = {
    "Breakfast": ("06:30", "10:45"),
    "Lunch":     ("10:45", "16:00"),
    "Dinner":    ("16:00", "21:30"),
}
```

The Pub runs 11:00–22:00 Monday to Thursday, so it now appears under both Lunch
and Dinner and not under Breakfast. The windows are deliberately generous at
the edges: being slightly wide costs you the Pub showing up at 2:15pm, being
narrow costs you a venue that is open and serving not appearing at all. A venue
that publishes no hours shows under every meal, for the same reason.

Hours also fix the Open/Closed pill. It used to be whatever NetNutrition
reported at scrape time, baked into `menu.json` — so a page opened at noon was
showing a 3am verdict. The browser now computes it against the reader's own
clock and says *how long*: "Open · til 10 PM", "Closed · opens 11 AM". Where a
venue publishes no hours the pill falls back to the scraped snapshot and stops
claiming to be live.

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

They also carry `_v`, the version of the parser that wrote them. A cache this
effective has a trap in it: improve the parser and *nothing already cached ever
sees the improvement*, because every lookup is a hit. Bumping
`NUTRITION_SCHEMA` puts stale entries back in the refetch queue, where they
drain through the normal per-run budget over a run or two.

### On accuracy

The label is read as published rather than tidied up:

- **`NA` stays null**, never zero. "No data" and "none of it" are different
  claims and the front-end shows them differently.
- **"less than 1g"** is recorded as its midpoint (0.5), not dropped and not
  rounded up to the bound.
- **Thousands separators are parsed.** `1,430mg` of sodium used to come back as
  1.0 — the amount regex stopped at the comma.
- **Partial totals are flagged, not published as totals.** NetNutrition marks a
  value it computed from a recipe with gaps in it; those keys are listed in
  `incomplete`, and the front-end stars them and says they are a minimum. On a
  typical day about 40% of recipes have at least one such value, so this is the
  difference between an honest number and a wrong one.
- **The gram weight is kept** (`grams`), which is the only thing on the label
  that makes "1 slice" and "4 oz. portion" comparable.

One assumption worth restating because everything rests on it: the same recipe
returns byte-identical nutrition from different halls. That was re-verified
against Rand and Commons on shared recipes, not just assumed.

## Taste of Nashville

These are off-campus restaurants, so they are not in NetNutrition at all — no
menus, no items, no nutrition. Vanderbilt publishes them as a plain
neighborhood-grouped list, which `scraper/restaurants.py` scrapes: inside the "Taste of
Nashville" section, a `<p>` ending in a colon names a neighborhood and the
`<li>` elements after it are that neighborhood's restaurants.

If the scrape returns fewer than five entries (a fetch failure, or a page
redesign), `main.py` keeps the previous run's list rather than blanking out a
working section of the site.

### Where the hours come from

Vanderbilt's page has **no** hours, addresses, links or map data — the entire
markup for an entry is `<li class="li2">Biscuit Love</li>`, and the only
structured block on the page is a CMS `NewsArticle`. So `scraper/places.py`
gets that detail from OpenStreetMap via the Overpass API: free, no API key, no
billing account, nothing secret to leak from a public repo.

Coverage is partial and the UI says so rather than guessing. Of the 36
partners, about 21 match an OSM entry, 16 have a street address and 12 have
`opening_hours`. **Every** restaurant gets a Google Maps *search* link, which
needs no API and lands on Google's own listing where live hours live — so the
one question that always matters ("where is it, is it open") always has an
answer.

Name matching is the delicate part, and a wrong match is worse than no match:

- OSM spells things differently (`Hyderabad House` vs `Nawabi Hyderabad
  House`, `Papa John's` vs `Papa John's Pizza`), so matching runs exact →
  substring → tight fuzzy (0.86), in that order.
- Chains have branches all over Nashville. Matching is anchored to the
  *neighborhood Vanderbilt listed*, not to campus — a campus radius alone
  still matched Biscuit Love to the Gulch and The Urban Juicer to
  Wedgewood-Houston. `PLACES_MAX_KM` is 1.5, measured rather than guessed: the
  furthest correct match is Inchin's at 1.03 km (the West End strip runs
  1800–2603) and the nearest incorrect one is 2.41 km, so the threshold sits
  in that gap.
- Anything past the limit is dropped, not shown.

Overpass is a donated, shared service, so the pipeline queries it at most once
every `PLACES_REFRESH_HOURS` (12) and carries the stored detail forward on the
runs in between — eight runs a day means two queries, not eight.

`opening_hours` is stored as OSM's raw string and parsed **in the browser**, so
"Open now" is live rather than frozen at build time. `parseHours()` in
`index.html` handles the subset these restaurants actually use — day selectors,
comma-separated ranges, semicolon rules, windows crossing midnight
(`Fr-Sa 10:00-01:00`), `24/7`, and the day-less continuation rule
(`Mo-Su 11:00-14:30; 17:00-21:30`). It returns null on anything it does not
understand, and an unparsed string is displayed verbatim rather than being
silently misread as "closed".

## The tracker

`docs/index.html` has a third view that logs what you eat. Hitting **+** on any
menu item records it with the macros it had at that moment; there's also a
manual-entry form for Taste of Nashville meals and anything else off-menu. It
shows today's totals against editable goals, today's log, a by-hour histogram of
when you actually eat, and calories per day over the last two weeks.

**The + button is a checkbox, not a submit button.** It used to log, flash a
tick for 1.2 seconds and reset to **+**, which meant that clicking it twice —
exactly what unchecking looks like — logged the same cheesecake twice, and the
only way back was to find it in the tracker's table. Now the row stays checked
for as long as it is in today's log, shows a count if you logged it more than
once, and a click on a checked row removes the most recent one. Deleting from
the tracker's table un-checks the menu row, so the two views cannot disagree.

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
  "meal_order":    ["Breakfast", "Lunch", "Dinner", "Daily Offerings"],
  "all_day_meals": ["Daily Offerings"],       // no meal period of their own
  "meal_windows":  { "Lunch": ["10:45", "16:00"], "…": [] },
  "halls": [{
    "id": 1, "name": "Rand Dining Center", "menu_count": 4,
    "status": "Closed",                       // the scrape-time snapshot
    "hours": {                                // what the browser uses instead
      "Monday": [["07:00", "15:00"]],
      "Friday": [["07:00", "15:00"], ["16:00", "20:00"]],
      "Sunday": []                            // published, and closed
    }
  }],
  "places_checked": "2026-09-02T03:40:38+00:00",   // last Overpass query
  "restaurants": [{
    "neighborhood": "Hillsboro Village", "name": "Hopdoddy Burger Bar",
    "maps": "https://www.google.com/maps/search/?api=1&query=…",  // always present
    "address": "1805 21st Avenue South",         // these five only when
    "hours": "Su-Th 11:00-22:00; Fr-Sa 11:00-23:00",  // OSM knows the place
    "website": "https://www.hopdoddy.com/…", "phone": "+1-615-823-2337",
    "cuisine": "burger", "lat": 36.136257, "lon": -86.801092
  }],
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
      "grams": 268.0, "servings_per_container": 1.0,
      "calories": 630.0, "fat": 28.0, "sat_fat": 14.0, "trans_fat": 0.0,
      "cholesterol": 60.0, "sodium": 1430.0, "carbs": 69.0, "fiber": 4.0,
      "sugars": 7.0, "added_sugars": null, "protein": 31.0,
      "calcium": 775.0, "iron": 5.0, "potassium": 40.0,
      "vitamin_c": 2.0, "vitamin_d": null,
      "ingredients": "Dough Pizza Crust White (ENRICHED FLOUR …",
      "contains": "Dairy, Gluten",
      "incomplete": ["sugars", "calcium"],  // computed from a partial recipe
      "_seen": "2026-09-01",
      "_v": 2                               // parser version, see below
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
  hours.py            each venue's published week of service hours
  restaurants.py      the Taste of Nashville partner list
  places.py           OpenStreetMap lookup: address, hours, website, phone
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
| `NUTRITION_SCHEMA` | bump it when the label parser improves; stale entries refetch |
| `MEAL_WINDOWS` | when each meal happens, which decides where all-day menus appear |
| `ALL_DAY_MEALS` | the menu names that carry no meal period of their own |
| `RESTAURANTS_URL` | the Taste of Nashville page |
| `PLACES_MAX_KM` | how far a match may sit from its neighborhood before it's rejected |
| `PLACES_FUZZ` | minimum similarity for the fuzzy name fallback |
| `PLACES_REFRESH_HOURS` | how often to re-query Overpass |
| `NEIGHBORHOOD_CENTERS` | the anchor point for each neighborhood |
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

Restaurant hours come from OpenStreetMap, which is volunteer-maintained and can
be stale — the card links straight to Google Maps, which is the thing to trust
before you walk somewhere. Tags and nutrition are whatever Vanderbilt
publishes; **confirm allergens with dining staff**. Items Vanderbilt has no recipe for show "no label" rather than a
guess — the footer says what fraction of the day's items have real numbers
behind them. Facilities with no published menu for the captured window (Wasabi,
the Suzie's locations, the food truck) simply don't appear. If a run suddenly
returns zero menus, CBORD changed their markup — check the selectors in
`scraper/fetcher.py`.
