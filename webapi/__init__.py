"""Helpers behind the JSON API (api.py).

`payloads` builds every read response from SQLite alone - no network, no
FastAPI - so each one is unit-testable against a temp database. `live` holds
the parts that must touch market data (placing a trade, re-pricing open
positions) and the writes.
"""
