"""
config.py — every knob for Anderson Dining lives here.

Nothing in this file requires an API key. The whole pipeline runs on
GitHub Actions' free tier against Vanderbilt's public NetNutrition site.
"""

# The CBORD NetNutrition instance Vanderbilt Campus Dining publishes to.
# Everything else in this project is derived from this one URL.
BASE_URL = "https://netnutrition.cbord.com/nn-prod/vucampusdining"

# NetNutrition sits behind an ASP.NET session + an AWS ALB. A default
# python-requests User-Agent gets served differently (and is impolite anyway),
# so identify as a real browser and say who we are in the comment trail.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Seconds to sleep between requests. Be a polite scraper: a full run is
# ~20 unit selects + ~100 menu fetches, and at 0.6s that is a couple of
# minutes of gentle traffic rather than a burst.
REQUEST_DELAY = 0.6

# Per-request timeout (seconds).
TIMEOUT = 30

# How many days forward to capture, counting today. 2 = today + tomorrow.
# NetNutrition publishes ~2 weeks ahead, but each extra day is another
# ~100 requests and ~1 MB of JSON, so keep this small.
DAYS_AHEAD = 2

# Meals to keep, in the order they should be displayed. Anything the site
# offers that is not in this list still gets captured; this only controls
# sort order (unknown meals sort last, alphabetically).
MEAL_ORDER = ["Breakfast", "Lunch", "Dinner", "Daily Offerings"]

# Menus that carry no meal period of their own. The Pub, the Munchie Marts and
# Local Java publish one all-day menu called "Daily Offerings", so filtering by
# "Lunch" used to hide them outright even while they were open and serving.
# The front-end shows an all-day menu under any meal whose window overlaps the
# venue's published hours.
ALL_DAY_MEALS = ["Daily Offerings"]

# When each meal period actually happens on campus, as (open, close) in 24-hour
# local time. Deliberately generous at the edges: this decides whether an
# all-day menu counts as lunch, and the cost of being slightly wide (the Pub
# shows up at 2:15 PM) is far lower than the cost of being narrow (a venue that
# is open and serving does not appear at all).
MEAL_WINDOWS = {
    "Breakfast": ("06:30", "10:45"),
    "Lunch": ("10:45", "16:00"),
    "Dinner": ("16:00", "21:30"),
}

# Vanderbilt is US Central. GitHub Actions runs in UTC, so "today" has to be
# resolved in campus time or a 03:00 UTC run reports tomorrow's menu.
CAMPUS_TZ = "America/Chicago"

# ---------------------------------------------------------------------------
# Nutrition
# ---------------------------------------------------------------------------

# NetNutrition publishes a full FDA label per item, one request each, which
# would be ~1300 requests a run — far too many. See nutrition.py for why the
# cache is keyed on the recipe (name + serving size) rather than on the item's
# detailOid: OIDs are per-serving and churn daily, recipes do not.
NUTRITION_CACHE_PATH = "docs/nutrition.json"

# Drop a cached recipe once it has gone this long without appearing on any
# menu, so the committed cache stays bounded as Vanderbilt retires dishes.
NUTRITION_TTL_DAYS = 120

# Hard ceiling on new labels fetched per run, so one bad day (a full menu
# rollover) can't turn a 3-minute Action into a 40-minute one and blow the
# job timeout. Anything skipped is simply picked up by the next run.
NUTRITION_BUDGET = 400

# Nutrient rows we lift out of the label, mapped to the short keys used in
# menu.json and the front-end. The label's own wording is on the left.
NUTRIENT_FIELDS = {
    "Calories": "calories",
    "Total Fat": "fat",
    "Saturated Fat": "sat_fat",
    "Trans Fat": "trans_fat",
    "Cholesterol": "cholesterol",
    "Sodium": "sodium",
    "Total Carbohydrate": "carbs",
    "Dietary Fiber": "fiber",
    "Total Sugars": "sugars",
    "Added Sugars": "added_sugars",
    "Protein": "protein",
    # The label's secondary table. Abbreviated exactly as the site writes them.
    "Vit. D": "vitamin_d",
    "Calcium": "calcium",
    "Iron": "iron",
    "Potas.": "potassium",
    "Vitamin C": "vitamin_c",
}

# Bump when parse_label starts extracting something new. Cached entries stamped
# with an older version are refetched (inside the usual budget) rather than
# being served forever at the old shape — otherwise a parser improvement only
# ever reaches recipes Vanderbilt happens to introduce afterwards.
NUTRITION_SCHEMA = 2


# --- OpenStreetMap enrichment ---------------------------------------------
#
# Vanderbilt's page lists names only. Addresses, websites and opening hours
# come from OpenStreetMap through Overpass: free, no API key, no billing, and
# so nothing secret has to live in a public repo. Coverage is partial — see
# places.py — and the front-end is explicit about that rather than guessing.

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Overpass is donation-funded and shared. Identify the client honestly; an
# anonymous flood is what gets IP ranges blocked.
OVERPASS_UA = "anderson-dining/1.0 (+https://github.com/Kratosxx24/anderson-dining)"

# (south, west, north, east) around campus and the four partner neighborhoods.
# Wide enough to catch Elliston Place and Midtown, tight enough that a chain's
# suburban branch never outranks the one students actually walk to.
PLACES_BBOX = (36.10, -86.86, 36.20, -86.73)

PLACES_TIMEOUT = 60

# Minimum similarity for the fuzzy name fallback. Kept high on purpose: a loose
# threshold is how you end up publishing the wrong restaurant's hours.
PLACES_FUZZ = 0.86

# Vanderbilt already tells us which neighborhood each partner is in, so use
# that rather than one radius around campus. Chains have branches all over
# Nashville and several are within walking distance of each other: a campus
# radius alone still matched Biscuit Love to the Gulch and The Urban Juicer to
# Wedgewood-Houston. Anchoring to the listed neighborhood fixes both.
NEIGHBORHOOD_CENTERS = {
    "Hillsboro Village": (36.1356, -86.7992),
    "Midtown":           (36.1505, -86.7936),
    "West End Avenue":   (36.1491, -86.8067),
    "Elliston Place":    (36.1521, -86.8058),
    "On Campus":         (36.1447, -86.8027),
}

# How far a match may sit from its neighborhood's center, in km. Measured, not
# guessed: against the real list the furthest *correct* match is Inchin's at
# 1.03 km (the West End strip runs 1800-2603, so it is long), and the nearest
# *incorrect* one is Biscuit Love's Gulch branch at 2.41 km. 1.5 sits in that
# gap. A wrong match is worse than no match, so err tight when in doubt.
PLACES_MAX_KM = 1.5

# Fallback radius from campus for a neighborhood we have no center for (say
# Vanderbilt adds one), so a new neighborhood degrades rather than breaks.
PLACES_FALLBACK_KM = 2.5

# Re-query Overpass at most this often. The menus change daily and this data
# changes far less, so eight runs a day should not mean eight Overpass queries.
PLACES_REFRESH_HOURS = 12

# ---------------------------------------------------------------------------
# Taste of Nashville
# ---------------------------------------------------------------------------

# The off-campus partner restaurants where Meal Money works. These are not in
# NetNutrition at all — they are a plain list on the dining site, grouped by
# neighborhood, with no menus or nutrition data behind them.
RESTAURANTS_URL = (
    "https://www.vanderbilt.edu/dining/where-to-dine/off-campus-dining/"
)

# The page keeps going after the restaurant list (food trucks, Kosher/Halal
# notes, policy). Stop collecting when a heading matches one of these.
RESTAURANTS_STOP_HEADINGS = [
    "On-Campus Food Trucks",
    "Kosher and Halal Options",
    "Taste of Nashville Policy",
]

# Dietary/allergen icons Vanderbilt attaches to items. The scraper reads
# whatever the site sends (from each icon's alt text), so this list is only
# used to give the front-end a stable, ordered set of filter chips.
KNOWN_TAGS = [
    "Vegetarian",
    "Vegan",
    "Halal",
    "Dairy",
    "Egg",
    "Fish",
    "Gluten",
    "Peanut",
    "Pork",
    "Sesame",
    "Shellfish",
    "Soy",
    "Tree Nut",
]
