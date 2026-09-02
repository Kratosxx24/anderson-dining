"""
fetcher.py — talks to Vanderbilt's NetNutrition instance and turns it into
structured menu data.

NetNutrition is not a REST API. It is a session-based ASP.NET MVC app whose
"AJAX" endpoints return rendered HTML fragments wrapped in a JSON envelope:

    {"success": true, "panels": [{"id": "menuPanel", "html": "<section>..."}]}

So the flow is: hold a requests.Session (for ASP.NET_SessionId + the AWS ALB
cookie), POST to advance the UI state, and parse the HTML back out of the
envelope. Three calls, in this order:

  1. GET  /                              -> session cookies + the unit list
  2. POST /Unit/SelectUnitFromUnitsList  {unitOid}  -> that hall's date/meal grid
  3. POST /Menu/SelectMenu               {menuOid}  -> the actual item table

A fourth call, NutritionDetail/ShowItemNutritionLabel, fetches one item's FDA
label; it is driven from nutrition.py, which caches the results.

The IDs are not in any URL — unit OIDs live in onclick="unitsSelectUnit(N)"
and menu OIDs in onclick="menuListSelectMenu(N)", both of which we regex out.

Selecting a unit resets the session's menu context, so step 2 has to be
repeated before each hall's batch of step-3 calls.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

try:  # Python 3.9+; the Action pins 3.11.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback for very old runtimes
    ZoneInfo = None

# onclick="javascript:NetNutrition.UI.unitsSelectUnit(11);"
UNIT_OID_RE = re.compile(r"unitsSelectUnit\((\d+)\)")
# onclick="javascript:NetNutrition.UI.menuListSelectMenu(9125337);"
MENU_OID_RE = re.compile(r"menuListSelectMenu\((\d+)\)")
# onclick="javascript:NetNutrition.UI.getItemNutritionLabelOnClick(event,282517631);"
# That second argument is the item's detailOid — the key to its nutrition label.
DETAIL_OID_RE = re.compile(r"getItemNutritionLabel(?:OnClick|FromKeyUp)\(event,\s*(\d+)\)")


class NetNutrition:
    """A single browsing session against the NetNutrition site."""

    def __init__(self):
        self.base = config.BASE_URL.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})
        # NetNutrition's own JS sends these; without X-Requested-With the
        # endpoints answer with a full page instead of the JSON envelope.
        self.ajax_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.base + "/",
        }

    # -- transport ---------------------------------------------------------

    def _post_panels(self, path: str, data: dict) -> dict:
        """POST an endpoint and return {panel_id: html} from the envelope."""
        time.sleep(config.REQUEST_DELAY)
        resp = self.session.post(
            f"{self.base}/{path}",
            data=data,
            headers=self.ajax_headers,
            timeout=config.TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success", False):
            raise RuntimeError(f"{path} returned success=false for {data}")
        return {p["id"]: p.get("html", "") for p in payload.get("panels", [])}

    # -- step 1: the unit list --------------------------------------------

    def open_session(self) -> list[dict]:
        """GET the landing page. Returns the dining halls it advertises."""
        resp = self.session.get(self.base, timeout=config.TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        units = []
        for link in soup.select("a.cbo_nn_unitNameLink"):
            match = UNIT_OID_RE.search(link.get("onclick", ""))
            if not match:
                continue
            # The open/closed pill is the sibling badge in the same card-header.
            status = ""
            header = link.find_parent(class_="card-header")
            if header:
                badge = header.select_one("a.badge")
                if badge:
                    status = badge.get_text(strip=True)
            units.append(
                {
                    "oid": int(match.group(1)),
                    "name": link.get_text(strip=True),
                    "status": status,
                }
            )
        return units

    # -- step 2: a hall's date/meal grid ----------------------------------

    def list_menus(self, unit_oid: int) -> list[dict]:
        """Select a hall; return every (date, meal, menu_oid) it publishes."""
        panels = self._post_panels(
            "Unit/SelectUnitFromUnitsList", {"unitOid": unit_oid}
        )
        html = panels.get("menuPanel", "")
        if not html.strip():
            return []
        soup = BeautifulSoup(html, "html.parser")

        menus = []
        # Each date is one <section class="card"> whose card-title is the date
        # and whose cbo_nn_menuLink anchors are that date's meal periods.
        for card in soup.select("section.card"):
            header = card.select_one(".card-title")
            if not header:
                continue
            date_iso = _parse_date(header.get_text(strip=True))
            if not date_iso:
                continue
            for link in card.select("a.cbo_nn_menuLink"):
                match = MENU_OID_RE.search(link.get("onclick", ""))
                if not match:
                    continue
                menus.append(
                    {
                        "oid": int(match.group(1)),
                        "date": date_iso,
                        "meal": link.get_text(strip=True),
                    }
                )
        return menus

    # -- step 3: the item table -------------------------------------------

    def fetch_menu(self, menu_oid: int) -> list[dict]:
        """Fetch one menu. Returns [{name, items: [...]}, ...] by category."""
        panels = self._post_panels("Menu/SelectMenu", {"menuOid": menu_oid})
        return parse_items(panels.get("itemPanel", ""))

    # -- step 4: one item's nutrition label -------------------------------

    def fetch_nutrition_label(self, detail_oid: int, menu_oid: int) -> str:
        """Fetch the raw FDA-label HTML for one item.

        Unlike the other endpoints this one answers with a bare HTML fragment,
        not the {"success":..., "panels":[...]} envelope, so it bypasses
        _post_panels. Returns "" when the site has no label for the item.
        """
        time.sleep(config.REQUEST_DELAY)
        resp = self.session.post(
            f"{self.base}/NutritionDetail/ShowItemNutritionLabel",
            data={"detailOid": detail_oid, "menuOid": menu_oid},
            headers=self.ajax_headers,
            timeout=config.TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text


def parse_items(html: str) -> list[dict]:
    """Parse an itemPanel fragment into ordered categories of items.

    The table is flat, not nested: a `cbo_nn_itemGroupRow` announces a course
    ("Pizza") and every `cbo_nn_itemPrimaryRow` after it belongs to that course
    until the next group row. Item rows are collapsed (`style='display:none'`)
    in the browser, which does not matter to us.
    """
    if not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")

    categories: list[dict] = []
    current: dict | None = None

    for row in soup.select("tr.cbo_nn_itemGroupRow, tr.cbo_nn_itemPrimaryRow"):
        classes = row.get("class", [])
        if "cbo_nn_itemGroupRow" in classes:
            current = {"name": row.get_text(" ", strip=True), "items": []}
            categories.append(current)
            continue

        link = row.select_one("a.cbo_nn_itemHover")
        if link is None:
            continue
        # The detailOid identifies this food across every menu and date it
        # appears on, which is what makes the nutrition cache worth keeping.
        detail_match = DETAIL_OID_RE.search(link.get("onclick", ""))
        detail_oid = int(detail_match.group(1)) if detail_match else None
        # The item name is the anchor's own text; the dietary icons that
        # follow it are <img> children whose alt/title carry the tag names.
        name = "".join(
            str(node) for node in link.find_all(string=True, recursive=False)
        ).strip()
        if not name:
            continue
        tags = []
        for img in link.find_all("img"):
            tag = (img.get("alt") or img.get("title") or "").strip()
            if tag and tag not in tags:
                tags.append(tag)

        # Columns: [checkbox/add] [name] [serving size] [# of servings] [...]
        cells = row.find_all("td")
        serving_size = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""

        if current is None:  # a menu with no course headers at all
            current = {"name": "Menu", "items": []}
            categories.append(current)
        current["items"].append(
            {
                "name": name,
                "serving_size": serving_size,
                "tags": tags,
                "detail_oid": detail_oid,
            }
        )

    return [c for c in categories if c["items"]]


def _parse_date(text: str) -> str | None:
    """'Tuesday, September 1, 2026' -> '2026-09-01'. None if unparseable."""
    text = text.strip()
    for fmt in ("%A, %B %d, %Y", "%A, %B %d %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def campus_today():
    """Today's date in Vanderbilt's timezone, not the runner's UTC.

    zoneinfo needs the IANA tz database, which ships with Linux but not with
    Windows or slim containers — and there it raises ZoneInfoNotFoundError at
    *lookup* time, not import time. requirements.txt pins `tzdata` so this
    should never fire, but falling back to UTC would silently report tomorrow's
    menu for the last five hours of every campus day, so fall back to a fixed
    Central offset and say so loudly instead.
    """
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(config.CAMPUS_TZ)).date()
        except Exception as exc:  # ZoneInfoNotFoundError and friends
            print(f"  ! no tz database for {config.CAMPUS_TZ} ({exc}); "
                  f"falling back to a fixed UTC-6 offset")
    return (datetime.now(timezone.utc) - timedelta(hours=6)).date()


def _meal_sort_key(meal: str):
    order = config.MEAL_ORDER
    return (order.index(meal), "") if meal in order else (len(order), meal)


def fetch_all() -> dict:
    """Walk every dining hall and return the full payload's data sections."""
    nn = NetNutrition()
    units = nn.open_session()
    print(f"Found {len(units)} dining facilities")

    today = campus_today()
    wanted = {
        (today + timedelta(days=n)).isoformat() for n in range(config.DAYS_AHEAD)
    }

    halls = []
    menus = []
    for unit in units:
        hall = {
            "id": unit["oid"],
            "name": unit["name"],
            "status": unit["status"],
            "menu_count": 0,
        }
        halls.append(hall)

        try:
            available = nn.list_menus(unit["oid"])
        except Exception as exc:
            print(f"  ! {unit['name']}: unit select failed ({exc})")
            continue

        targets = [m for m in available if m["date"] in wanted]
        targets.sort(key=lambda m: (m["date"], _meal_sort_key(m["meal"])))
        if not targets:
            print(f"  - {unit['name']}: no menus published for {sorted(wanted)}")
            continue

        for menu in targets:
            try:
                categories = nn.fetch_menu(menu["oid"])
            except Exception as exc:
                print(f"  ! {unit['name']} {menu['date']} {menu['meal']}: {exc}")
                continue
            if not categories:
                continue
            item_count = sum(len(c["items"]) for c in categories)
            menus.append(
                {
                    "hall": unit["name"],
                    "hall_id": unit["oid"],
                    # Kept so the nutrition backfill has a menu to ask against;
                    # a label POST needs both a detailOid and some menuOid.
                    "menu_oid": menu["oid"],
                    "date": menu["date"],
                    "meal": menu["meal"],
                    "item_count": item_count,
                    "categories": categories,
                }
            )
            hall["menu_count"] += 1
            print(
                f"  + {unit['name']} / {menu['date']} / {menu['meal']}: "
                f"{item_count} items in {len(categories)} categories"
            )

    # The live session goes back with the data: the nutrition backfill reuses
    # it rather than paying for a fresh handshake and unit list.
    return {"halls": halls, "menus": menus, "session": nn}
