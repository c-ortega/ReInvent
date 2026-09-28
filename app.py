from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import html
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

st.markdown("""
<style>
  [data-testid="stTabs"] button { font-weight: 650; }
  [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px;
  }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge[data-tone="workshop"]) { background: #fff8ee; border-color: #f3d5ac; }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge[data-tone="talk"]) { background: #f3f7ff; border-color: #c9d9f5; }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge[data-tone="builder"]) { background: #effaf7; border-color: #b9e4d8; }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge[data-tone="keynote"]) { background: #fff2f0; border-color: #f0c7c0; }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge[data-tone="other"]) { background: #f6f3fc; border-color: #d9ccec; }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge) { height: 260px; box-sizing: border-box; overflow-y: auto; }
  [data-testid="stVerticalBlockBorderWrapper"]:has(.format-badge):has(details[open]) { height: auto; overflow: visible; }
  .format-badge { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: .78rem; font-weight: 700; }
  .format-badge[data-tone="workshop"] { color: #89500d; background: #ffebca; }
  .format-badge[data-tone="talk"] { color: #28518d; background: #e2edff; }
  .format-badge[data-tone="builder"] { color: #176756; background: #d9f3eb; }
  .format-badge[data-tone="keynote"] { color: #983f31; background: #ffe0da; }
  .format-badge[data-tone="other"] { color: #62478d; background: #ece3fa; }
  .format-badge[data-tone="default"] { color: #475569; background: #e9eef4; }
  .meta-badge { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: .76rem; font-weight: 650; }
  .venue-0 { color: #245b76; background: #dff2fa; }
  .venue-1 { color: #8a5218; background: #fff0d8; }
  .venue-2 { color: #4a5e9b; background: #e8ebff; }
  .venue-3 { color: #376b44; background: #e3f3e4; }
  .venue-4 { color: #734a78; background: #f3e7f5; }
  .venue-5 { color: #9a4545; background: #fbe7e5; }
  .venue-unknown { color: #475569; background: #e9eef4; }
  .level-foundation { color: #286344; background: #e1f4e8; }
  .level-intermediate { color: #28518d; background: #e2edff; }
  .level-advanced { color: #62478d; background: #ece3fa; }
  .level-expert { color: #983f31; background: #ffe0da; }
  .level-distinguished { color: #475569; background: #e9eef4; }
  .badge-key { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 4px 0 12px; }
  .badge-key-label { color: #68778a; font-size: .78rem; font-weight: 700; margin-right: 3px; }
  .session-time { display: inline-block; padding: 5px 9px; margin: 2px 0 7px; border-radius: 8px; color: #263b55; background: #eaf0f7; font-size: .8rem; font-weight: 700; }
</style>
""", unsafe_allow_html=True)


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(localTimeZone)


def clock(iso: str) -> str:
    return local(iso).strftime("%I:%M %p").lstrip("0")


def labelDay(day: str) -> str:
    return datetime.fromisoformat(day).strftime("%A, %b %d")


def formatTone(sessionType: str) -> str:
    value = sessionType.casefold()
    if "workshop" in value or "lab" in value:
        return "workshop"
    if "keynote" in value or "address" in value:
        return "keynote"
    if "builder" in value:
        return "builder"
    if any(word in value for word in ("breakout", "chalk", "lecture", "talk", "session")):
        return "talk"
    return "other" if value else "default"


def formatBadge(sessionType: str) -> str:
    label = sessionType or "Format TBD"
    return f'<span class="format-badge" data-tone="{formatTone(label)}">{html.escape(label)}</span>'


def formatDetails(session: dict) -> str:
    venue = html.escape(session["venue"] or "Venue TBD")
    level = html.escape(session["level"] or "")
    venueIndex = {"caesars-forum": 0, "caesars-palace": 1, "encore": 2,
                  "mgm-grand": 3, "venetian": 4, "wynn": 5}.get(venueId(session["venue"]), "unknown")
    levelValue = (session["level"] or "").casefold()
    levelTone = next((tone for words, tone in [
        (("foundational", "foundation", "beginner", "introductory"), "foundation"),
        (("intermediate",), "intermediate"),
        (("advanced",), "advanced"),
        (("expert",), "expert"),
    ] if any(word in levelValue for word in words)), "distinguished")
    venueBadge = f'<span class="meta-badge venue-{venueIndex}">{venue}</span>'
    levelBadge = f'<span class="meta-badge level-{levelTone}">{level or "Level TBD"}</span>'
    return f"{formatBadge(session['type'])}  {venueBadge}  {levelBadge}"


def sessionTiming(session: dict) -> str:
    dateTime = f"{labelDay(session['day'])}  ·  {clock(session['start'])}–{clock(session['end'])}"
    return f'<span class="session-time">{html.escape(dateTime)}</span>'


def showBadgeKey():
    with st.expander("Color key", expanded=False):
        st.markdown("""
        <div class="badge-key">
          <span class="badge-key-label">Venues</span>
          <span class="meta-badge venue-0">Caesars Forum</span><span class="meta-badge venue-1">Caesars Palace</span>
          <span class="meta-badge venue-2">Encore</span><span class="meta-badge venue-3">MGM Grand</span>
          <span class="meta-badge venue-4">Venetian</span><span class="meta-badge venue-5">Wynn</span>
          <span class="meta-badge venue-unknown">Other</span>
          <span class="badge-key-label">Levels</span>
          <span class="meta-badge level-foundation">Foundational</span><span class="meta-badge level-intermediate">Intermediate</span>
          <span class="meta-badge level-advanced">Advanced</span><span class="meta-badge level-expert">Expert</span>
          <span class="meta-badge level-distinguished">Distinguished</span>
        </div>
        """, unsafe_allow_html=True)


def toggleSavedSession(sessionId: str):
    current = set(db.getSetting("savedSessions", []))
    if sessionId in current:
        current.discard(sessionId)
    else:
        current.add(sessionId)
    db.setSetting("savedSessions", sorted(current))


def resetCatalogPage():
    st.session_state["catalogPage"] = 1


def changeCatalogPage(delta: int):
    current = st.session_state.get("catalogPage", 1)
    count = st.session_state.get("catalogPageCount", 1)
    st.session_state["catalogPage"] = max(1, min(count, current + delta))


def showCatalogPagination(first: int, last: int, total: int, location: str):
    previous, summary, nextPage = st.columns([1, 4, 1])
    previous.button("← Previous", key=f"catalogPrevious{location}",
                    disabled=st.session_state["catalogPage"] <= 1,
                    on_click=changeCatalogPage, args=(-1,), use_container_width=True)
    summary.markdown(f"<div style='text-align:center;padding:.45rem 0'>Showing <b>{first}–{last}</b> of <b>{total}</b> sessions · Page {st.session_state['catalogPage']} of {st.session_state['catalogPageCount']}</div>", unsafe_allow_html=True)
    nextPage.button("Next →", key=f"catalogNext{location}",
                    disabled=st.session_state["catalogPage"] >= st.session_state["catalogPageCount"],
                    on_click=changeCatalogPage, args=(1,), use_container_width=True)


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
savedSessionIds: set[str] = set(db.getSetting("savedSessions", []))
days = sorted({s["day"] for s in sessions})
saved = db.getSetting("dailyLimits", {})
savedKeywords = db.getSetting("keywords", "")

catalogTab, priorityTab, plannerTab = st.tabs(["Browse sessions", "Prioritize saved", "Build schedules"])
with catalogTab:
    query = st.text_input("Search title, description, service, or topic", placeholder="Bedrock, .NET, architecture…",
                          key="catalogQuery", on_change=resetCatalogPage)
    c1, c2, c3 = st.columns(3)
    dayFilter = c1.selectbox("Day", ["All days", *days], format_func=lambda d: labelDay(d) if d != "All days" else d,
                             key="catalogDay", on_change=resetCatalogPage)
    types = sorted({s["type"] for s in sessions if s["type"]})
    typeFilter = c2.selectbox("Format", ["All formats", *types], key="catalogFormat", on_change=resetCatalogPage)
    levels = sorted({s["level"] for s in sessions if s["level"]})
    levelFilter = c3.selectbox("Level", ["All levels", *levels], key="catalogLevel", on_change=resetCatalogPage)
    needle = query.casefold().strip()
    matches = [s for s in sessions if
               (not needle or needle in " ".join([s["title"], s["abstract"], s["code"], *s["topics"], *s["services"]]).casefold())
               and (dayFilter == "All days" or s["day"] == dayFilter)
               and (typeFilter == "All formats" or s["type"] == typeFilter)
               and (levelFilter == "All levels" or s["level"] == levelFilter)]
    matches.sort(key=lambda s: (s["day"], local(s["start"]), s["title"]))
    st.caption(f"{len(matches)} matching sessions · Save sessions here, then set priorities on the next page.")
    showBadgeKey()
    pageCount = max(1, (len(matches) + 39) // 40)
    st.session_state["catalogPageCount"] = pageCount
    page = min(st.session_state.get("catalogPage", 1), pageCount)
    st.session_state["catalogPage"] = page
    visible = matches[(page - 1) * 40:page * 40]
    firstResult = (page - 1) * 40 + 1 if matches else 0
    lastResult = min(page * 40, len(matches))
    showCatalogPagination(firstResult, lastResult, len(matches), "Top")
    cardColumns = st.columns(3, gap="medium")
    for index, session in enumerate(visible):
        with cardColumns[index % 3]:
            with st.container(border=True):
                titleColumn, starColumn = st.columns([0.86, 0.14])
                isSaved = session["id"] in savedSessionIds
                _, _, starAction = starColumn.columns([2, 1, 1])
                titleColumn.markdown(f"**{session['title']}**")
                starAction.button(" ", icon=":material/star:" if isSaved else ":material/star_outline:",
                                  key=f"save{session['id']}",
                                  help="Remove from saved sessions" if isSaved else "Save this session",
                                  on_click=toggleSavedSession, args=(session["id"],),
                                  type="secondary", use_container_width=True)
                st.caption(session["code"] or "SESSION")
                st.markdown(sessionTiming(session), unsafe_allow_html=True)
                st.markdown(formatDetails(session), unsafe_allow_html=True)
                with st.expander("About this session"):
                    st.write(session["abstract"] or "No description available.")
                    tags = session["services"] + session["topics"]
                    if tags:
                        st.caption("  ·  ".join(tags))
    showCatalogPagination(firstResult, lastResult, len(matches), "Bottom")

with priorityTab:
    savedSessions = [s for s in sessions if s["id"] in savedSessionIds]
    savedSessions.sort(key=lambda s: (s["day"], local(s["start"]), s["title"]))
    st.caption(f"{len(savedSessions)} saved sessions · Set a rating or mark must-attend sessions, then save your priorities.")
    if savedSessions:
        showBadgeKey()
    if not savedSessions:
        st.info("Browse sessions and save a few to get started.")
    else:
        with st.form("ratingsForm"):
            edits = []
            cardColumns = st.columns(3, gap="medium")
            for index, session in enumerate(savedSessions):
                with cardColumns[index % 3]:
                    with st.container(border=True):
                        st.markdown(f"**{session['title']}**")
                        st.caption(session["code"] or "SESSION")
                        st.markdown(sessionTiming(session), unsafe_allow_html=True)
                        st.markdown(formatDetails(session), unsafe_allow_html=True)
                        a, b = st.columns([1, 1])
                        rating = a.selectbox("Priority", [-1, 0, 1, 2, 3, 4, 5],
                                             index=[-1, 0, 1, 2, 3, 4, 5].index(int(ratings.get(session["id"], 0))),
                                             format_func=lambda n: "Avoid" if n == -1 else "Neutral" if n == 0 else f"{n} / 5",
                                             key=f"rating{session['id']}")
                        must = b.checkbox("Must attend", value=session["id"] in locked, key=f"lock{session['id']}")
                        edits.append((session["id"], rating, must))
            if st.form_submit_button("Save priorities", type="primary"):
                for sid, rating, must in edits:
                    ratings[sid] = rating
                    if must:
                        locked.add(sid)
                    else:
                        locked.discard(sid)
                db.setSetting("ratings", ratings)
                db.setSetting("locked", sorted(locked))
                st.session_state.pop("options", None)
                st.success("Priorities saved. Generate schedules on the next page.")

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
                with st.container(border=True):
                    st.caption(f"STOP {index + 1}  ·  {clock(s['start'])}–{clock(s['end'])}  ·  {s['code'] or 'SESSION'}")
                    st.markdown(f"**{s['title']}**")
                    st.markdown(f"📍 {s['venue'] or 'Venue TBD'}{('  ·  ' + s['room']) if s['room'] else ''}")
                if index + 1 < len(items):
                    nxt = items[index + 1]
                    travel = minutes(s["venue"], nxt["venue"])
                    gap = int((local(nxt["start"]) - local(s["end"])).total_seconds() // 60)
                    st.caption(f"↓  ~{travel} min transfer  ·  {gap - travel} min buffer" + ("  ⚠ Tight connection" if gap - travel < 10 else ""))
        with right:
            st.subheader("Venue map · session order")
            mapFor(items)
    elif not submitted:
        st.info("Set your per-day counts and click **Generate alternative schedules**.")
