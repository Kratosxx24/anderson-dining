"""
hours.py — each venue's real opening hours.

NetNutrition's landing page shows an Open/Closed pill per dining hall, and
that pill is a link: clicking it POSTs the unit to
`Unit/GetHoursOfOperationMarkup` and drops a weekday table into a modal.
Vanderbilt's own dining site points students at exactly that pill when they
ask about hours, so it is the authoritative published source.

Two things in this project need it.

First, the pill itself. The scrape bakes "Closed" into menu.json at whatever
hour the Action happened to run, so a page opened at noon was reporting a
3 a.m. verdict. With the week's hours in hand the browser computes open/closed
against the reader's own clock instead.

Second, and the reason this file exists at all: several venues — The Pub at
Overcup Oak, the Munchie Marts, Local Java — publish a single menu called
"Daily Offerings" with no meal period attached. Filtering by "Lunch" therefore
hid them entirely, even though the Pub is open 11 AM to 10 PM and is serving
that food through both lunch and dinner. Hours are what let the front-end say
"this all-day menu is available at lunch" without guessing.

The markup is a plain <table>: one row per service block, `[day, open, close]`,
or `[day, "Closed"]`. A day can appear twice (Rand runs a lunch block and a
separate Friday dinner block), so the parse collects a *list* of ranges per day
rather than one.
"""

import re

from bs4 import BeautifulSoup

from . import config

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# "3:00 PM" / "11:59 PM" / "7 AM". Minutes are optional in principle; the site
# has always sent them, but a missing ":00" should not drop a whole venue.
TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?[Mm]\.?$")


def to_minutes(text: str):
    """'3:00 PM' -> 900 (minutes past midnight). None if unparseable."""
    match = TIME_RE.match((text or "").replace("\xa0", " ").strip())
    if not match:
        return None
    hour = int(match.group(1)) % 12
    minute = int(match.group(2) or 0)
    if match.group(3).lower() == "p":
        hour += 12
    return hour * 60 + minute


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hours(html: str) -> dict:
    """Parse the modal's table into {"Monday": [["07:00","15:00"], ...], ...}.

    Days the venue is closed map to an empty list, which is a meaningful
    answer — distinct from a day the site said nothing about, which is absent.
    """
    if not html or not html.strip():
        return {}
    soup = BeautifulSoup(html, "html.parser")

    week: dict[str, list] = {}
    for row in soup.select("tr"):
        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        if len(cells) < 2:
            continue
        day = cells[0].strip()
        if day not in DAYS:
            continue
        week.setdefault(day, [])

        opens = to_minutes(cells[1])
        closes = to_minutes(cells[2]) if len(cells) > 2 else None
        if opens is None or closes is None:
            # "Closed", or a free-text note like "By appointment" — either way
            # there is no range to record for this block.
            continue
        # A block that ends at or before it starts is an overnight service
        # (the Munchies close at 11:59 PM, so this is rare but not impossible).
        # Record it as running to end-of-day rather than dropping it.
        if closes <= opens:
            closes = 24 * 60 - 1
        week[day].append([_hhmm(opens), _hhmm(closes)])

    return week


def fetch_hours(nn, unit_oid: int) -> dict:
    """Fetch and parse one venue's week. Returns {} if the site has none.

    `nn` is an open fetcher.NetNutrition session; this endpoint answers with a
    bare HTML fragment rather than the panels envelope, like the nutrition
    label does.
    """
    html = nn.post_raw("Unit/GetHoursOfOperationMarkup", {"unitOid": unit_oid})
    return parse_hours(html)


# -- what the front-end asks of it -----------------------------------------


def serves_meal(week: dict, weekday: str, meal: str) -> bool:
    """Is this venue open during `meal`'s window on `weekday`?

    Used for all-day ("Daily Offerings") menus, which carry no meal period of
    their own. Unknown hours answer True: showing an all-day menu under every
    meal is a far smaller error than hiding a venue that is actually serving.
    """
    window = config.MEAL_WINDOWS.get(meal)
    if not window:
        return True
    ranges = (week or {}).get(weekday)
    if not ranges:
        return not week  # no data at all -> assume open; explicit closed -> no
    start, end = to_minutes_pair(window)
    for opens, closes in ranges:
        o, c = to_minutes_24(opens), to_minutes_24(closes)
        if o is None or c is None:
            continue
        if o < end and c > start:  # any overlap at all
            return True
    return False


def to_minutes_24(text: str):
    """'07:00' -> 420. The stored format, not the site's 12-hour one."""
    try:
        hour, minute = text.split(":")
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError):
        return None


def to_minutes_pair(window) -> tuple:
    return to_minutes_24(window[0]), to_minutes_24(window[1])
