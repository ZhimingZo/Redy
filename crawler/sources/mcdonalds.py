"""McDonald's corporate roles use the same Paradox career site as FedEx.

Restaurant hiring is a different board (jobs.mchire.com) and is not read here.
The corporate list is https://careers.mcdonalds.com/jobs.
"""
from __future__ import annotations

from .fedex import fetch as _fetch

_JOBS = ("https://careers.mcdonalds.com/jobs",)


def fetch(base: dict, get=None) -> list[dict]:
    return _fetch(base, get, searches=_JOBS, platform="mcdonalds")
