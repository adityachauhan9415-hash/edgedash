# EdgeDash

EdgeDash is an autonomous career intelligence agent that runs on a daily schedule.
It fetches live job listings from configured sources, scores each listing for fit
against your profile, identifies skill gaps between where you are and where the
roles require you to be, verifies its own output for consistency, and publishes
everything to a read-only Streamlit dashboard — with no manual steps in between.

---

## Architecture

```
Trigger (scheduled)
    └── Orchestrator
            ├── Fetcher
            ├── Scorer
            └── GapAnalyzer
                    └── Verifier
                            └── Storage
                                    └── Dashboard (read-only)
```

The Orchestrator reads state and delegates; it never fetches, scores, or analyses
directly. Each agent has one goal and one stop condition. The Dashboard never
writes to Storage.

---

## Current status

### Week 1 — skeleton (complete)

- [x] `Agent` ABC and `AgentResult` contract (`edgedash/agents/base.py`)
- [x] MockFetcher — 12 fake listings, 4 stable IDs for dedup testing *(temporary — replaced in week 2)*
- [x] SQLite storage layer with `listings`, `cycle_log`, and `state` tables
- [x] Orchestrator with agent registry, state read, plan printer, and cycle summary
- [x] `run_cycle.py` entry point
- [x] Deduplication verified: second run reports 8 new / 4 known

### Week 2 — live data

- [ ] Real Fetcher: Adzuna or RapidAPI Jobs source, rate-limited, retry logic
- [ ] Stable listing IDs derived from `sha256(company + title + url)` so the same
      posting doesn't create duplicate rows across fetches
- [ ] Config-driven source list (`config.yaml`)

### Week 3 — scoring and gap analysis

- [ ] Scorer: cosine similarity between listing skills and profile skills, returns
      0–1 score with per-skill breakdown
- [ ] GapAnalyzer: diff between listing required skills and profile skills, grouped
      by frequency across all listings
- [ ] Verifier: schema checks, score range guards, gap completeness checks

### Week 4 — dashboard

- [ ] Streamlit dashboard reading from Storage (read-only)
- [ ] Views: top-scored listings, skill gap heatmap, fetch history
- [ ] Scheduler integration (cron or APScheduler)

---

## Setup

**Requirements:** Python 3.11+

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd edgedash

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and edit as needed:

```
EDGEDASH_TARGET_ROLE=Data Engineer
EDGEDASH_CITY=San Francisco, CA
EDGEDASH_FETCH_AGENT=MockFetcher
EDGEDASH_DB_PATH=edgedash.db
EDGEDASH_PROFILE_PATH=profile.yaml
```

All keys are optional; the values above are the defaults.

Edit `profile.yaml` to describe your current skills and target seniority.
The Scorer and GapAnalyzer read this file — no code changes needed when
your profile changes.

### Run one cycle

```bash
python run_cycle.py
```

The console prints the pipeline state it read, the plan it chose and why,
each agent's result as it completes, and a summary table. Run it twice to
watch deduplication in action.

---

## Design decisions

**Storage is isolated behind one module.**
All reads and writes go through `edgedash/storage.py`. No agent imports
`sqlite3` directly. This means the database schema, file path, and query
logic can all change without touching agent code, and the Dashboard is
structurally prevented from writing — it only has access to `read_*` methods.

**Listing IDs are stable hashes.**
Once the real Fetcher is in place, each listing's ID will be derived from
`sha256(company + title + canonical_url)` rather than a UUID or a source
system's own ID. The same job posting re-fetched the next day produces the
same ID, so `upsert_listings` treats it as an update rather than a new row.
This keeps the listings table from growing unboundedly and makes dedup
provable without a separate seen-set.

**The Orchestrator delegates instead of doing the work itself.**
Keeping the Orchestrator free of domain logic means each agent can be
developed, tested, and replaced independently. The Orchestrator's only
job is to read state, decide which agents to invoke and in what order,
and record what happened. If scoring logic changes, only the Scorer
changes. If the fetch source changes, only the Fetcher changes.
The Orchestrator's code stays stable across all of it.
