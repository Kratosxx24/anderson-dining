"""
main.py — the orchestrator. Run this and it does the whole pipeline:
scrape NetNutrition -> normalize -> write docs/menu.json

The GitHub Action runs this on a schedule; you can also run it locally with
`python main.py`. No API keys, no paid services.
"""

import json
import os
from datetime import datetime, timezone

import config
import fetcher

OUTPUT_PATH = os.path.join("docs", "menu.json")


def main():
    print("=" * 60)
    print(f"ANDERSON DINING RUN — {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    data = fetcher.fetch_all()
    menus = data["menus"]

    # Only surface tags the scrape actually saw, but keep KNOWN_TAGS' order so
    # the filter chips don't reshuffle between runs.
    seen = {tag for m in menus for c in m["categories"] for i in c["items"] for tag in i["tags"]}
    tags = [t for t in config.KNOWN_TAGS if t in seen]
    tags += sorted(seen - set(tags))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campus_date": fetcher.campus_today().isoformat(),
        "source": config.BASE_URL,
        "menu_count": len(menus),
        "item_count": sum(m["item_count"] for m in menus),
        "tags": tags,
        "halls": data["halls"],
        "menus": menus,
    }

    os.makedirs("docs", exist_ok=True)
    # Write to a temp file first so a crash mid-write can't leave the site
    # serving truncated JSON.
    tmp = OUTPUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, OUTPUT_PATH)

    print(
        f"\nWrote {payload['menu_count']} menus "
        f"({payload['item_count']} items) to {OUTPUT_PATH}"
    )
    print("Done.")


if __name__ == "__main__":
    main()
