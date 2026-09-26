from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

eventId = "reinvent2026"
catalogUrl = f"https://api.awsevents.com/v1/events/{eventId}/sessions"
eventTimeZone = ZoneInfo("America/Los_Angeles")


def firstValue(obj: dict, *names: str) -> Any:
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    return None


def asText(value: Any) -> str:
    if isinstance(value, dict):
        return str(firstValue(value, "name", "label", "title", "value", "displayName", "code") or "")
    return str(value or "")


def asLabels(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [label for item in value if (label := asText(item).strip())]


def parseTimestamp(value: Any) -> str | None:
    if isinstance(value, dict):
        value = firstValue(value, "dateTime", "timestamp", "startTime", "endTime", "value")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None  # Avoid silently scheduling in the machine's time zone.
        return parsed.isoformat()
    except ValueError:
        return None


def parseSessionTime(value: Any) -> tuple[str, str] | None:
    """Read AWS's date, local time, duration and optional time zone."""
    if not isinstance(value, dict):
        return None
    date, clock, length = (value.get(key) for key in ("date", "time", "length"))
    if not all(isinstance(part, str) and part for part in (date, clock, length)):
        return None
    zoneName = value.get("timezone")
    try:
        if not zoneName:
            zone = eventTimeZone
        elif zoneName in ("PST", "PDT", "PT", "Pacific Standard Time", "Pacific Daylight Time"):
            zone = eventTimeZone
        elif isinstance(zoneName, str) and len(zoneName) == 6 and zoneName[0] in "+-" and zoneName[3] == ":":
            sign = 1 if zoneName[0] == "+" else -1
            zone = timezone(sign * timedelta(hours=int(zoneName[1:3]), minutes=int(zoneName[4:6])))
        else:
            zone = ZoneInfo(str(zoneName))
        duration = int(length)
        if not 0 < duration <= 24 * 60:
            return None
        start = datetime.fromisoformat(f"{date}T{clock}")
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        end = start + timedelta(minutes=duration)
        return start.isoformat(), end.isoformat()
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None


def normalize(raw: dict) -> dict | None:
    """Return one timed occurrence, or None if it cannot safely be scheduled."""
    if raw.get("isAllDaySession"):
        return None
    sid = firstValue(raw, "sessionId", "id")
    title = asText(firstValue(raw, "title", "name"))
    start = parseTimestamp(firstValue(raw, "startTime", "start", "startsAt"))
    end = parseTimestamp(firstValue(raw, "endTime", "end", "endsAt"))
    if not (start and end):
        parsedTime = parseSessionTime(raw.get("sessionTime"))
        if parsedTime:
            start, end = parsedTime
    if not (sid and title and start and end):
        return None
    if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
        return None
    venue = asText(firstValue(raw, "venue", "venueName", "location"))
    room = asText(firstValue(raw, "room", "roomName"))
    code = asText(firstValue(raw, "abbreviation", "code", "sessionCode", "shortCode"))
    return {
        "id": str(sid), "code": code, "title": title,
        "abstract": asText(firstValue(raw, "abstract", "description")),
        "type": asText(firstValue(raw, "type", "sessionType")),
        "level": asText(firstValue(raw, "level", "sessionLevel")),
        "topics": asLabels(raw.get("topics")) + asLabels(raw.get("areasOfInterest")),
        "services": asLabels(firstValue(raw, "services", "awsServices")),
        "tracks": asLabels(raw.get("tracks")),
        "start": start, "end": end, "venue": venue, "room": room,
        "day": datetime.fromisoformat(start).astimezone(eventTimeZone).date().isoformat(),
    }


def fetchAll(accessToken: str) -> list[dict]:
    """Walk nextToken until absent; preserve raw AWS objects for later normalization."""
    result: dict[str, dict] = {}
    nextToken = None
    seenTokens: set[str] = set()
    with httpx.Client(timeout=35, headers={"Authorization": f"Bearer {accessToken}"}) as client:
        while True:
            params: dict[str, str] = {"includeAbstracts": "true"}
            if nextToken:
                params["nextToken"] = nextToken
            response = client.get(catalogUrl, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Unexpected AWS catalog response: expected an object")
            page = payload.get("items", payload.get("sessions"))
            if not isinstance(page, list):
                keys = ", ".join(sorted(payload))
                raise ValueError(f"Unexpected AWS catalog response: items list missing (fields: {keys})")
            for item in page:
                if isinstance(item, dict) and item.get("sessionId") is not None:
                    result[str(item["sessionId"])] = item
            nextToken = payload.get("nextToken")
            if not nextToken:
                return list(result.values())
            if not isinstance(nextToken, str) or nextToken in seenTokens:
                raise ValueError("AWS returned an invalid or repeated nextToken")
            seenTokens.add(nextToken)

