from __future__ import annotations

import json
import math
from pathlib import Path

projectRoot = Path(__file__).resolve().parent
venues = json.loads((projectRoot / "data/venues.json").read_text())
travelConfig = json.loads((projectRoot / "data/travelTimes.json").read_text())


def venueId(name: str) -> str | None:
    key = " ".join((name or "").lower().replace("the ", "").split())
    if not key:
        return None
    for vid, details in venues.items():
        canonical = details["name"].lower().replace("the ", "")
        if canonical in key or key in canonical or vid.replace("-", " ") in key:
            return vid
    return None


def minutes(a: str, b: str) -> int:
    if a.strip().casefold() == b.strip().casefold() and a.strip():
        return int(travelConfig["sameVenueMinutes"])
    aid, bid = venueId(a), venueId(b)
    if aid and aid == bid:
        return int(travelConfig["sameVenueMinutes"])
    if not aid or not bid:
        return int(travelConfig["unknownVenueMinutes"])
    override = travelConfig["overrides"].get("|".join(sorted((aid, bid))))
    if override is not None:
        return int(override)
    x, y = venues[aid], venues[bid]
    lat1, lat2 = math.radians(x["lat"]), math.radians(y["lat"])
    dlat, dlon = lat2 - lat1, math.radians(y["lon"] - x["lon"])
    arc = 2 * math.asin(math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2))
    km = 6371 * arc * float(travelConfig["routeMultiplier"])
    return max(12, math.ceil(km / float(travelConfig["walkingSpeedKmh"]) * 60 + float(travelConfig["entryExitMinutes"])))
