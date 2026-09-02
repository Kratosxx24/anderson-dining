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

# Vanderbilt is US Central. GitHub Actions runs in UTC, so "today" has to be
# resolved in campus time or a 03:00 UTC run reports tomorrow's menu.
CAMPUS_TZ = "America/Chicago"

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
