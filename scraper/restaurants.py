"""
restaurants.py — the Taste of Nashville partner list.

Taste of Nashville is the program that lets a Meal Money balance be spent at
off-campus restaurants. Those restaurants are not in NetNutrition at all — no
menus, no items, no nutrition — they are a plain neighborhood-grouped list on
Vanderbilt's dining site, so this is a separate, much simpler scrape.

The page's markup is unremarkable WordPress output: inside the "Taste of
Nashville" section, a <p> ending in a colon names a neighborhood and the <li>
elements that follow it are that neighborhood's restaurants, until the next
such <p>. The section ends at the next <h2> (food trucks, Kosher/Halal notes,
the alcohol policy), which is what RESTAURANTS_STOP_HEADINGS lists.
"""

import requests
from bs4 import BeautifulSoup

from . import config

# The heading that opens the section we want.
START_HEADING = "Taste of Nashville"


def fetch_restaurants() -> list:
    """Scrape the partner list. Returns [{neighborhood, name}, ...] in order.

    Raises nothing on a thin result — an empty list is a legitimate answer if
    Vanderbilt reworks the page, and main.py decides what to do about it.
    """
    resp = requests.get(
        config.RESTAURANTS_URL,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.TIMEOUT,
    )
    resp.raise_for_status()
    # WordPress serves UTF-8 but is not always explicit about it, and the names
    # carry curly apostrophes (Jeni's, Helen's) that mangle if we guess wrong.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    main = soup.find("main") or soup

    return parse_restaurants(main)


def parse_restaurants(main) -> list:
    """Walk the section's block elements in document order and collect names."""
    found = []
    neighborhood = None
    started = False

    for el in main.find_all(["h1", "h2", "h3", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue

        if el.name in ("h1", "h2", "h3"):
            if text == START_HEADING:
                started = True
                continue
            # Any other heading after we've started ends the section. Checking
            # the configured list first keeps an unexpected sub-heading inside
            # the section from silently truncating the list.
            if started and any(
                text.startswith(stop) for stop in config.RESTAURANTS_STOP_HEADINGS
            ):
                break
            continue

        if not started:
            continue

        # "Hillsboro Village:" — a neighborhood header, not a restaurant.
        if el.name == "p":
            if text.endswith(":") and len(text) < 60:
                neighborhood = text[:-1].strip()
            continue

        if el.name == "li" and neighborhood:
            # The list markup nests <li> oddly in places; skip anything that
            # is clearly prose rather than a name.
            if len(text) > 60:
                continue
            found.append({"neighborhood": neighborhood, "name": text})

    # The same restaurant is occasionally listed under two neighborhoods; keep
    # the first listing and drop exact repeats so the UI doesn't show doubles.
    seen = set()
    unique = []
    for entry in found:
        if entry["name"].casefold() in seen:
            continue
        seen.add(entry["name"].casefold())
        unique.append(entry)
    return unique
