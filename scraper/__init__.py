"""
scraper — the data pipeline behind Anderson Dining.

Each module owns one source or concern:

  config       every tunable knob, and nothing else
  fetcher      the NetNutrition session: halls, menus, item rows
  hours        each venue's published service hours
  nutrition    FDA labels, their parsing, and the recipe-keyed cache
  restaurants  the Taste of Nashville partner list

`main.py` at the repo root is the entry point that wires them together and
writes docs/. Nothing in here writes files except nutrition's cache helpers.
"""
