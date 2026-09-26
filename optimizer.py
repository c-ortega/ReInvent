from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ortools.sat.python import cp_model

from travel import minutes

localTimeZone = ZoneInfo("America/Los_Angeles")
maxCandidatesPerDay = 85
profiles = {
    "Balanced": (2, 2, 115, 0),
    "Best content": (3, 1, 85, 0),
    "Less walking": (2, 5, 100, 0),
    "Relaxed": (2, 4, 45, 25),
}


def value(session: dict, ratings: dict[str, int], keywords: list[str]) -> int:
    rating = int(ratings.get(session["id"], 0))
    text = " ".join([session["title"], session["abstract"], *session["topics"], *session["services"]]).casefold()
    hits = sum(1 for word in keywords if word.casefold() in text)
    level = session["level"]
    advanced = 8 if "300" in level or "400" in level else 0
    return 15 + 25 * rating + 20 * min(hits, 3) + advanced


def toLocal(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(localTimeZone)


def selectCandidates(sessions, ratings, keywords, locks, limits):
    byDay = defaultdict(list)
    for s in sessions:
        if s["day"] in limits and (ratings.get(s["id"], 0) >= 0 or s["id"] in locks):
            byDay[s["day"]].append(s)
    selected = []
    for day, items in sorted(byDay.items()):
        top = sorted(items, key=lambda s: (s["id"] in locks, value(s, ratings, keywords)), reverse=True)
        selected += top[:maxCandidatesPerDay]
        selected += [s for s in top[maxCandidatesPerDay:] if s["id"] in locks]
    return selected


def generate(
    sessions: list[dict], dailyLimits: dict[str, dict[str, int]],
    ratings: dict[str, int], keywords: list[str], lockedIds: set[str],
    lunch: tuple[str, int] | None = None,
) -> tuple[list[dict], str | None]:
    """Return schedule alternatives and an explanatory warning, if needed.

    min/max are hard constraints; target is a soft objective. An infeasible
    request yields no schedules rather than silently changing the limits.
    """
    if not dailyLimits:
        return [], "No dated sessions are available. Refresh your catalog first."
    for day, l in dailyLimits.items():
        if not (0 <= l["min"] <= l["target"] <= l["max"]):
            return [], f"{day}: minimum, target, and maximum must be in order."
    ids = {s["id"] for s in sessions}
    missing = lockedIds - ids
    if missing:
        return [], f"{len(missing)} locked session(s) are no longer in the catalog; unlock them first."
    candidates = selectCandidates(sessions, ratings, keywords, lockedIds, dailyLimits)
    byDay = defaultdict(list)
    for i, s in enumerate(candidates):
        byDay[s["day"]].append(i)
    for day, l in dailyLimits.items():
        if len(byDay[day]) < l["min"]:
            return [], f"{day}: only {len(byDay[day])} eligible timed sessions; minimum is {l['min']}."

    results = []
    previous: list[set[str]] = []
    warning = None
    for name, (contentWeight, travelWeight, targetWeight, extraBuffer) in profiles.items():
        # A diversity constraint makes the options meaningful. Retry without it
        # if very tight hard constraints permit only the same selection.
        option = solveOption(candidates, byDay, dailyLimits, ratings, keywords,
                        lockedIds, lunch, contentWeight, travelWeight,
                        targetWeight, extraBuffer, previous)
        if option is None and previous:
            option = solveOption(candidates, byDay, dailyLimits, ratings, keywords,
                            lockedIds, lunch, contentWeight, travelWeight,
                            targetWeight, extraBuffer, [])
        if option is None:
            if not results:
                return [], ("No feasible schedule satisfies the locks, travel buffers, lunch block, "
                            "repeat limits, and daily minimum/maximum counts. Try lowering a minimum "
                            "or removing a lock.")
            warning = "Some options could not satisfy the current constraints."
            continue
        chosen = option
        if chosen in previous:
            warning = "The current constraints allow fewer distinct options than requested."
            continue
        previous.append(chosen)
        chosenSessions = sorted((s for s in candidates if s["id"] in chosen),
                                 key=lambda s: toLocal(s["start"]))
        results.append({"name": name, "sessions": chosenSessions, "stats": statistics(chosenSessions, dailyLimits)})
    return results, warning


def solveOption(sessions, byDay, limits, ratings, keywords, locks, lunch,
           contentWeight, travelWeight, targetWeight, extraBuffer, previous):
    model = cp_model.CpModel()
    x = [model.NewBoolVar(f"s{i}") for i in range(len(sessions))]
    for i, s in enumerate(sessions):
        if s["id"] in locks:
            model.Add(x[i] == 1)

    objective = [contentWeight * value(s, ratings, keywords) * x[i]
                 for i, s in enumerate(sessions)]
    byCode = defaultdict(list)
    for i, s in enumerate(sessions):
        if s["code"]:
            byCode[s["code"].casefold()].append(i)
    for repeated in byCode.values():
        if len(repeated) > 1:
            model.AddAtMostOne(x[i] for i in repeated)

    for day, indices in byDay.items():
        bounds = limits[day]
        count = sum(x[i] for i in indices)
        model.Add(count >= bounds["min"])
        model.Add(count <= bounds["max"])
        deviation = model.NewIntVar(0, len(indices) + bounds["target"], f"dev_{day}")
        model.AddAbsEquality(deviation, count - bounds["target"])
        objective.append(-targetWeight * deviation)

        lunchStart = None
        if lunch and lunch[1]:
            hour, minute = map(int, lunch[0].split(":"))
            lunchStart = datetime.fromisoformat(day).replace(
                hour=hour, minute=minute, tzinfo=localTimeZone)
        for a, i in enumerate(indices):
            si = sessions[i]
            startI, endI = toLocal(si["start"]), toLocal(si["end"])
            if lunchStart and startI < lunchStart + timedelta(minutes=lunch[1]) and endI > lunchStart:
                model.Add(x[i] == 0)
            for j in indices[a + 1:]:
                sj = sessions[j]
                startJ, endJ = toLocal(sj["start"]), toLocal(sj["end"])
                if startI > startJ:
                    before, after = (sj, si)
                    finish, begin = endJ, startI
                else:
                    before, after = (si, sj)
                    finish, begin = endI, startJ
                gap = (begin - finish).total_seconds() / 60
                travel = minutes(before["venue"], after["venue"])
                if gap < travel + extraBuffer:
                    model.Add(x[i] + x[j] <= 1)
                elif gap <= 120 and before["venue"] != after["venue"]:
                    # Nearby selections influence walking preference. The final
                    # timeline reports only actual consecutive transfers.
                    pair = model.NewBoolVar(f"pair_{i}_{j}")
                    model.Add(pair <= x[i])
                    model.Add(pair <= x[j])
                    model.Add(pair >= x[i] + x[j] - 1)
                    objective.append(-travelWeight * travel * pair)
    for day, bounds in limits.items():
        if day not in byDay and bounds["min"]:
            return None
    for prior in previous:
        overlap = [x[i] for i, s in enumerate(sessions) if s["id"] in prior]
        if len(overlap) > len(locks) + 1:
            model.Add(sum(overlap) <= len(overlap) - min(2, len(overlap) - len(locks)))
    model.Maximize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 8
    solver.parameters.num_search_workers = 4
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {s["id"] for i, s in enumerate(sessions) if solver.Value(x[i])}


def statistics(sessions, limits):
    byDay = defaultdict(list)
    for session in sessions:
        byDay[session["day"]].append(session)
    walking = changes = 0
    transfers = {}
    for day, items in byDay.items():
        items.sort(key=lambda s: toLocal(s["start"]))
        for a, b in zip(items, items[1:]):
            estimate = minutes(a["venue"], b["venue"])
            walking += estimate
            changes += a["venue"] != b["venue"]
            transfers[(a["id"], b["id"])] = estimate
    return {"count": len(sessions), "travel": walking, "changes": changes,
            "days": {day: len(byDay[day]) for day in limits}, "transfers": transfers}
