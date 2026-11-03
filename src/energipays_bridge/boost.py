"""The one boost-period table for the UI, the REST API, MQTT and the CLI (#3).

energipays.com sends period 2 / 3 / 4 for its 1 h / 2 h / 3 h buttons, and period 1 to
cancel (energipays-client ``BOOST_PERIODS``, docs/API.md). The bridge never sends 1 as
a duration.
"""
from __future__ import annotations

DURATIONS: dict[str, int] = {"1 hour": 2, "2 hours": 3, "3 hours": 4}
PERIOD_LABEL: dict[int, str] = {p: name for name, p in DURATIONS.items()}
DEFAULT_DURATION = "1 hour"
VALID_PERIODS = tuple(PERIOD_LABEL)
