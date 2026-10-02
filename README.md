# Personal AWS re:Invent planner

A local Python/Streamlit app to browse re:Invent 2026 sessions, choose your interests and must-attend sessions, and compare alternative itineraries on a timeline and venue map.

## Project layout

`app.py`, `auth.py`, `catalog.py`, `database.py`, `optimizer.py`, and `travel.py` are **directly inside `reinventPlanner/`**. The `data/` directory holds only JSON files in the download:

```text
ReInvent/
├── app.py
├── auth.py
├── catalog.py
├── database.py
├── optimizer.py
├── travel.py
├── requirements.txt
├── data/
│   ├── sampleSessions.json
│   ├── travelTimes.json
│   └── venues.json
└── tests/
    └── testPlanner.py
```

When the app runs, `database.py` creates `data/planner.db`. After successful AWS sign-in, `auth.py` creates `data/tokens.json`. Neither runtime file is included in the download. There is no `data/token.py` or `data/database.py`.

## Run

Use Python 3.11 or newer:

```bash
cd reinventPlanner
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py --server.address localhost
```

Open the Streamlit URL on **the same computer** as the Python process. AWS's OAuth callback is `http://localhost:8484/callback`; that port must be free. This flow cannot be completed from a remote browser against a hosted Streamlit instance. You need to be registered for re:Invent 2026 with the Builder ID used to sign in. Press **Start AWS sign-in**, follow **Continue to AWS Builder ID**, then return to the planner and press **Refresh catalog from AWS**. For a quick offline preview, press **Load sample sessions** (synthetic data clearly labeled `DEMO`).

## Plan your days

1. In **Browse sessions**, search and filter the catalog, then save sessions with the star.
2. In **Prioritize saved**, rate saved sessions 1–5, mark sessions **Avoid**, or lock must-attend sessions. Save your priorities.
3. In **Build schedules**, enter preferred keywords and a minimum, target, and maximum number of sessions **for each day**. Set all three equal to require an exact count. Optionally reserve a fixed lunch block.
4. Click **Generate alternative schedules**. Compare counts and estimated travel across Balanced, Best content, Less walking, and Relaxed, then select an option and day to see its timeline and map. When the itinerary is final, click **Sync selected itinerary to AWS favorites** to add its sessions to your AWS account.

The solver always enforces minimum and maximum counts, locks, time overlaps, estimated transfers, and at most one occurrence of a repeated session code. The Relaxed option adds a 25-minute buffer beyond each estimated transfer. Targets and keyword matches influence ranking. If constraints conflict, it reports that no feasible itinerary exists instead of quietly breaking them. It tries to make options distinct; tight constraints can leave fewer than four choices.

## Files

| File                       | Purpose                                                    |
| -------------------------- | ---------------------------------------------------------- |
| `app.py`                   | Streamlit browser UI, timeline, comparison, and venue map  |
| `auth.py`                  | Local Builder ID OAuth with PKCE and token refresh         |
| `catalog.py`               | AWS catalog, schedule and favorites API calls; session normalization |
| `database.py`              | SQLite cache and local settings                            |
| `optimizer.py`             | OR-Tools CP-SAT scheduling and alternatives                |
| `travel.py`                | Venue matching and approximate transfer times              |
| `data/venues.json`         | Approximate venue centers used for pins                    |
| `data/travelTimes.json`    | Editable travel assumptions and overrides                  |
| `data/sampleSessions.json` | Synthetic sessions for preview and tests                   |

Keep the app local to your own account. On POSIX systems token files are written with owner-only permissions. **Sign out** removes the local tokens.

## Limits

- Venue pins are approximate centers and map lines connect them directly. They are **not pedestrian routes or indoor wayfinding**. The timeline's travel minutes are planning estimates from an editable configuration. Check real routes and shuttle times before relying on tight transfers.
- Syncing adds only the selected itinerary's sessions to AWS favorites; it keeps existing AWS favorites and does not reserve seats. AWS favorites are a planning list. Use the attendee portal or AWS Events app to reserve sessions when booking opens. Session availability can change.
- The solver considers the top 85 scored candidates per day plus all locked sessions so generation remains responsive. Rate favorites and add keywords to make sure relevant sessions are considered.
- Untimed or malformed sessions cannot be scheduled. The catalog count and usable timed count are shown separately.
- A public CARTO basemap style needs an internet connection for map tiles. The venue markers and itinerary are computed locally.
