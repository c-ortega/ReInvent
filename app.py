from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pydeck as pdk
import streamlit as st

import auth
import database as db
from catalog import fetchAll, normalize
from optimizer import generate
from travel import venues, minutes, venueId

localTimeZone = ZoneInfo("America/Los_Angeles")
st.set_page_config(page_title="re:Invent Planner", page_icon="🗺️", layout="wide")


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(localTimeZone)


def clock(iso: str) -> str:
    return local(iso).strftime("%I:%M %p").lstrip("0")


def labelDay(day: str) -> str:
    return datetime.fromisoformat(day).strftime("%A, %b %d")


def mapFor(items: list[dict]):
    grouped = defaultdict(list)
    unknown = set()
    for number, session in enumerate(items, 1):
        vid = venueId(session["venue"])
        if vid:
            grouped[vid].append((number, session))
        else:
            unknown.add(session["venue"] or "Unspecified venue")
    points = []
    for vid, sessions in grouped.items():
        location = venues[vid]
        points.append({
            "lat": location["lat"], "lon": location["lon"],
            "venue": location["name"],
            "stops": ", ".join(str(n) for n, _ in sessions),
            "titles": " • ".join(s["title"] for _, s in sessions),
        })
    paths = []
    for a, b in zip(items, items[1:]):
        va, vb = venueId(a["venue"]), venueId(b["venue"])
        if va and vb and va != vb:
            paths.append({"path": [
                [venues[va]["lon"], venues[va]["lat"]],
                [venues[vb]["lon"], venues[vb]["lat"]]],
                "from": a["title"], "to": b["title"]})
    layers = [
        pdk.Layer("PathLayer", paths, get_path="path", get_color=[42, 123, 217],
                  width_min_pixels=4, pickable=True),
        pdk.Layer("ScatterplotLayer", points, get_position="[lon, lat]",
                  get_fill_color=[244, 136, 50], get_line_color=[255, 255, 255],
                  line_width_min_pixels=2, get_radius=125, radius_min_pixels=10,
                  radius_max_pixels=18, pickable=True),
        pdk.Layer("TextLayer", points, get_position="[lon, lat]", get_text="stops",
                  get_color=[15, 30, 48], get_size=14, get_pixel_offset=[0, -26]),
    ]
    view = pdk.ViewState(latitude=36.116, longitude=-115.169, zoom=12.0, pitch=0)
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view,
                            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
                            tooltip={"html": "<b>{venue}</b><br/>Stops {stops}<br/>{titles}"}),
                    width="stretch")
    if unknown:
        st.caption("No map point for: " + ", ".join(sorted(unknown)))
    st.caption("Markers are approximate venue centers; lines are straight-line links, not walking directions. Travel minutes are editable estimates in data/travelTimes.json.")


st.title("AWS re:Invent 2026 planner")
st.caption("Personal schedule alternatives • Las Vegas times • local data")

with st.sidebar:
    st.header("Catalog")
    if auth.isSignedIn():
        st.success("AWS Builder ID connected")
        if st.button("Sign out"):
            auth.signOut()
            st.rerun()
    else:
        st.info("Sign in with the Builder ID registered for re:Invent to download sessions.")
        if st.button("Start AWS sign-in"):
            try:
                st.session_state["loginUrl"] = auth.beginSignIn()
            except RuntimeError as exc:
                st.error(str(exc))
        if st.session_state.get("loginUrl"):
            st.link_button("Continue to AWS Builder ID", st.session_state["loginUrl"])
            st.caption("After signing in, return here and refresh the catalog.")
    if st.button("Refresh catalog from AWS", disabled=not auth.isSignedIn()):
        try:
            token = auth.accessToken()
            if not token:
                raise RuntimeError("Sign in first.")
            with st.spinner("Downloading all catalog pages…"):
                raw = fetchAll(token)
                db.saveCatalog(raw)
                currentIds = {str(s["sessionId"]) for s in raw if s.get("sessionId") is not None}
                db.setSetting("locked", [sid for sid in db.getSetting("locked", []) if sid in currentIds])
            st.session_state.pop("options", None)
            st.success(f"Saved {len(raw)} catalog sessions.")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                st.error(f"AWS returned {code}. Sign in with the Builder ID registered for re:Invent.")
            else:
                st.error(f"AWS returned HTTP {code}. Try refreshing again.")
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            st.error(f"Catalog refresh failed: {exc}")
    if st.button("Load sample sessions"):
        import json
        from pathlib import Path
        sample = json.loads((Path(__file__).parent / "data/sampleSessions.json").read_text())
        db.saveCatalog(sample)
        sampleIds = {s["sessionId"] for s in sample}
        db.setSetting("locked", [sid for sid in db.getSetting("locked", []) if sid in sampleIds])
        st.session_state.pop("options", None)
        st.rerun()
    st.caption("Sample mode replaces the cached catalog. Refresh from AWS later to restore it.")

rawSessions = db.loadCatalog()
sessions = [s for raw in rawSessions if (s := normalize(raw))]
st.caption(f"{len(rawSessions)} cached catalog entries • {len(sessions)} timed sessions eligible for planning")
if not sessions:
    st.info("Use **Load sample sessions** to explore the interface, or sign in and refresh the AWS catalog.")
    st.stop()

ratings: dict[str, int] = db.getSetting("ratings", {})
locked: set[str] = set(db.getSetting("locked", []))
days = sorted({s["day"] for s in sessions})
saved = db.getSetting("dailyLimits", {})
savedKeywords = db.getSetting("keywords", "")

catalogTab, plannerTab = st.tabs(["Discover & prioritize", "Build schedules"])
with catalogTab:
    query = st.text_input("Search title, description, service, or topic", placeholder="Bedrock, .NET, architecture…")
    c1, c2, c3 = st.columns(3)
    dayFilter = c1.selectbox("Day", ["All days", *days], format_func=lambda d: labelDay(d) if d != "All days" else d)
    types = sorted({s["type"] for s in sessions if s["type"]})
    typeFilter = c2.selectbox("Format", ["All formats", *types])
    levels = sorted({s["level"] for s in sessions if s["level"]})
    levelFilter = c3.selectbox("Level", ["All levels", *levels])
    needle = query.casefold().strip()
    matches = [s for s in sessions if
               (not needle or needle in " ".join([s["title"], s["abstract"], s["code"], *s["topics"], *s["services"]]).casefold())
               and (dayFilter == "All days" or s["day"] == dayFilter)
               and (typeFilter == "All formats" or s["type"] == typeFilter)
               and (levelFilter == "All levels" or s["level"] == levelFilter)]
    matches.sort(key=lambda s: (s["day"], local(s["start"]), s["title"]))
    st.caption(f"{len(matches)} matching sessions. Rate favorites 1–5, avoid with −1, or lock a must-attend session. Showing up to 40 at a time.")
    pageCount = max(1, (len(matches) + 39) // 40)
    page = st.number_input("Page", min_value=1, max_value=pageCount, value=1)
    visible = matches[(page - 1) * 40:page * 40]
    with st.form("ratingsForm"):
        edits = []
        for session in visible:
            st.markdown(f"**{session['code'] or 'Session'} · {session['title']}**  \n{labelDay(session['day'])} · {clock(session['start'])}–{clock(session['end'])} · {session['venue'] or 'Venue TBD'} · {session['type']}")
            with st.expander("Description"):
                st.write(session["abstract"] or "No description available.")
                st.caption(" · ".join(session["services"] + session["topics"]))
            a, b = st.columns([2, 1])
            rating = a.selectbox("Priority", [-1, 0, 1, 2, 3, 4, 5],
                                 index=[-1, 0, 1, 2, 3, 4, 5].index(int(ratings.get(session["id"], 0))),
                                 format_func=lambda n: "Avoid" if n == -1 else "Neutral" if n == 0 else f"{n} / 5",
                                 key=f"rating{session['id']}")
            must = b.checkbox("Must attend", value=session["id"] in locked, key=f"lock{session['id']}")
            edits.append((session["id"], rating, must))
            st.divider()
        if st.form_submit_button("Save these priorities"):
            for sid, rating, must in edits:
                ratings[sid] = rating
                if must:
                    locked.add(sid)
                else:
                    locked.discard(sid)
            db.setSetting("ratings", ratings)
            db.setSetting("locked", sorted(locked))
            st.session_state.pop("options", None)
            st.success("Priorities saved. Generate schedules on the next tab.")

with plannerTab:
    st.subheader("Daily session counts")
    with st.form("planForm"):
        keywordText = st.text_input("Preferred topics or services (comma separated)", value=savedKeywords,
                                     help="A title, description, topic, or service match raises a session's score.")
        st.caption("Minimum and maximum are required. Target is preferred; exactly N means setting all three to N.")
        limits = {}
        for day in days:
            previous = saved.get(day, {"min": 0, "target": 5, "max": 8})
            st.markdown(f"**{labelDay(day)}**")
            a, b, c = st.columns(3)
            lower = a.number_input("Minimum", 0, 15, int(previous["min"]), key=f"min{day}")
            target = b.number_input("Target", 0, 15, int(previous["target"]), key=f"target{day}")
            upper = c.number_input("Maximum", 0, 15, int(previous["max"]), key=f"max{day}")
            limits[day] = {"min": int(lower), "target": int(target), "max": int(upper)}
        lunchEnabled = st.checkbox("Reserve a fixed lunch block", value=True)
        a, b = st.columns(2)
        lunchClock = a.selectbox("Lunch start", [f"{h:02d}:{m:02d}" for h in range(11, 15) for m in (0, 30)], index=3)
        lunchLength = b.number_input("Lunch minutes", 15, 120, 45, step=15)
        submitted = st.form_submit_button("Generate alternative schedules", type="primary")
    if submitted:
        if any(not (v["min"] <= v["target"] <= v["max"]) for v in limits.values()):
            st.error("Each day needs minimum ≤ target ≤ maximum.")
        else:
            db.setSetting("dailyLimits", limits)
            db.setSetting("keywords", keywordText)
            keywords = [word.strip() for word in keywordText.split(",") if word.strip()]
            with st.spinner("Solving alternative itineraries…"):
                options, warning = generate(sessions, limits, ratings, keywords, locked,
                                            (lunchClock, int(lunchLength)) if lunchEnabled else None)
            st.session_state["options"] = options
            st.session_state["planWarning"] = warning
    if st.session_state.get("planWarning"):
        st.warning(st.session_state["planWarning"])
    options = st.session_state.get("options", [])
    if options:
        st.subheader("Compare options")
        st.dataframe([{
            "Option": o["name"], "Sessions": o["stats"]["count"],
            "Estimated transfer minutes": o["stats"]["travel"],
            "Venue changes": o["stats"]["changes"],
            **{labelDay(day): count for day, count in o["stats"]["days"].items()},
        } for o in options], width="stretch", hide_index=True)
        selectedName = st.radio("Itinerary", [o["name"] for o in options], horizontal=True)
        selected = next(o for o in options if o["name"] == selectedName)
        selectedDay = st.selectbox("View day", days, format_func=labelDay)
        items = [s for s in selected["sessions"] if s["day"] == selectedDay]
        left, right = st.columns([1, 1.1], gap="large")
        with left:
            st.subheader(f"Timeline · {labelDay(selectedDay)}")
            if not items:
                st.write("No sessions planned for this day.")
            for index, s in enumerate(items):
                st.markdown(f"**{index + 1}. {clock(s['start'])}–{clock(s['end'])} · {s['code'] or 'Session'}**  \n{s['title']}  \n{s['venue'] or 'Venue TBD'}{(' · ' + s['room']) if s['room'] else ''}")
                if index + 1 < len(items):
                    nxt = items[index + 1]
                    travel = minutes(s["venue"], nxt["venue"])
                    gap = int((local(nxt["start"]) - local(s["end"])).total_seconds() // 60)
                    st.caption(f"↓ ~{travel} min transfer · {gap - travel} min buffer" + (" ⚠ tight" if gap - travel < 10 else ""))
                st.divider()
        with right:
            st.subheader("Venue map · session order")
            mapFor(items)
    elif not submitted:
        st.info("Set your per-day counts and click **Generate alternative schedules**.")
