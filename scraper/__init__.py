"""
scraper — the data pipeline behind Anderson Dining.

Four modules, each owning one source or concern:

  config       every tunable knob, and nothing else
  fetcher      the NetNutrition session: halls, menus, item rows
  nutrition    FDA labels, their parsing, and the recipe-keyed cache
  restaurants  the Taste of Nashville partner list

`main.py` at the repo root is the entry point that wires them together and
writes docs/. Nothing in here writes files except nutrition's cache helpers.
"""
