from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

eventId = "reinvent2026"
catalogUrl = f"https://api.awsevents.com/v1/events/{eventId}/sessions"


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


def normalize(raw: dict) -> dict | None:
    """Return one timed occurrence, or None if it cannot safely be scheduled."""
    sid = firstValue(raw, "sessionId", "id")
    title = asText(firstValue(raw, "title", "name"))
    start = parseTimestamp(firstValue(raw, "startTime", "start", "startsAt"))
    end = parseTimestamp(firstValue(raw, "endTime", "end", "endsAt"))
    if not (sid and title and start and end):
        return None
    if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
        return None
    venue = asText(firstValue(raw, "venue", "venueName", "location"))
    room = asText(firstValue(raw, "room", "roomName"))
    code = asText(firstValue(raw, "code", "sessionCode", "shortCode"))
    return {
        "id": str(sid), "code": code, "title": title,
        "abstract": asText(firstValue(raw, "abstract", "description")),
        "type": asText(firstValue(raw, "type", "sessionType")),
        "level": asText(firstValue(raw, "level", "sessionLevel")),
        "topics": asLabels(raw.get("topics")),
        "services": asLabels(firstValue(raw, "services", "awsServices")),
        "tracks": asLabels(raw.get("tracks")),
        "start": start, "end": end, "venue": venue, "room": room,
        "day": datetime.fromisoformat(start).astimezone(__import__("zoneinfo").ZoneInfo("America/Los_Angeles")).date().isoformat(),
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
            page = payload.get("sessions")
            if not isinstance(page, list):
                raise ValueError("Unexpected AWS catalog response: sessions list missing")
            for item in page:
                if isinstance(item, dict) and item.get("sessionId") is not None:
                    result[str(item["sessionId"])] = item
            nextToken = payload.get("nextToken")
            if not nextToken:
                return list(result.values())
            if not isinstance(nextToken, str) or nextToken in seenTokens:
                raise ValueError("AWS returned an invalid or repeated nextToken")
            seenTokens.add(nextToken)
