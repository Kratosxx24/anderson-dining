# Anderson Dining

Every Vanderbilt campus dining menu, scraped a few times a day into one
filterable page. Same free-forever architecture as `anderson-wire`:

```
GitHub Actions cron  ->  main.py  ->  docs/menu.json  ->  GitHub Pages
```

No servers, no database, no API keys, $0/month.

## What it does

`fetcher.py` walks Vanderbilt's NetNutrition site and pulls today's and
tomorrow's menus for every dining facility that publishes one — dining hall,
meal period, course/station, item name, serving size, and the dietary and
allergen tags Vanderbilt attaches to each item (Vegan, Halal, Gluten, Tree
Nut, …). `main.py` writes it all to `docs/menu.json`. `docs/index.html` reads
that file and renders it with filters for day, meal, "only show" traits, "hide"
allergens, and a text search.

## How the scrape works

NetNutrition is **not** a REST API. It's a session-based ASP.NET MVC app whose
AJAX endpoints return rendered HTML fragments inside a JSON envelope:

```json
{"success": true, "panels": [{"id": "menuPanel", "html": "<section>…"}]}
```

So the scraper holds a `requests.Session` (for `ASP.NET_SessionId` plus the AWS
ALB cookie) and makes three kinds of call:

| Step | Call | Gives you |
| --- | --- | --- |
| 1 | `GET /nn-prod/vucampusdining` | session cookies + the dining-hall list |
| 2 | `POST /Unit/SelectUnitFromUnitsList` `{unitOid}` | that hall's date × meal grid |
| 3 | `POST /Menu/SelectMenu` `{menuOid}` | the item table for one meal |

The IDs never appear in a URL. Unit OIDs live in
`onclick="…unitsSelectUnit(11)"` and menu OIDs in
`onclick="…menuListSelectMenu(9125337)"`, both regexed out of the HTML.
`X-Requested-With: XMLHttpRequest` is required — without it the endpoints
return a full page instead of the JSON envelope. Selecting a unit resets the
session's menu context, so step 2 is repeated before each hall's step-3 batch.

Inside `itemPanel` the table is flat, not nested: a `tr.cbo_nn_itemGroupRow`
announces a station ("Pizza") and every following `tr.cbo_nn_itemPrimaryRow`
belongs to it until the next group row. Dietary tags come from the `alt` text
of the icon images that follow the item name.

## Output shape

```jsonc
{
  "generated_at": "2026-09-01T20:10:00+00:00",
  "campus_date": "2026-09-01",          // "today" in Central time, not UTC
  "menu_count": 44,
  "item_count": 1266,
  "tags": ["Vegetarian", "Vegan", "…"], // every tag this run actually saw
  "halls": [{ "id": 1, "name": "Rand Dining Center", "status": "Closed", "menu_count": 4 }],
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

## Running it

```bash
pip install -r requirements.txt
python main.py            # writes docs/menu.json
```

A full run is ~120 requests spread over a couple of minutes.

## Configuration

Everything lives in `config.py`: `DAYS_AHEAD` (how far forward to capture — each
extra day is another ~100 requests), `REQUEST_DELAY` (politeness throttle),
`MEAL_ORDER`, and `KNOWN_TAGS` (only controls filter-chip ordering; the scraper
records whatever tags the site sends).

## Deploying

1. Push to GitHub.
2. Settings → Pages → source **Deploy from a branch**, branch `main`, folder `/docs`.
3. Settings → Actions → General → Workflow permissions → **Read and write**.

The workflow runs at ~4am / 8am / noon / 4pm / 8pm Central and commits
`docs/menu.json` back to the repo. Menus change daily, not hourly — don't
tighten that cron.

## Caveats

Tags are whatever Vanderbilt publishes; **confirm allergens with dining staff**.
Facilities with no published menu for the captured window (Wasabi, the Suzie's
locations, the food truck) simply don't appear. If a run suddenly returns zero
menus, CBORD changed their markup — check the selectors in `fetcher.py`.
