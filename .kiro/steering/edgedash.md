# EdgeDash — Project Steering Rules

## Project Identity

**EdgeDash** is an autonomous AI career intelligence agent. It runs as a scheduled loop that:

1. Fetches live job listings from configured sources
2. Scores each listing for fit against the user's profile
3. Surfaces skill gaps between the user's profile and target roles
4. Verifies its own output for correctness and consistency
5. Publishes results to a read-only Streamlit dashboard

---

## Architecture

The canonical data flow is fixed. Do not deviate from it without explicitly telling the user first and getting confirmation.

```
Trigger (scheduled)
    └── Orchestrator
            ├── Fetcher          (sub-agent)
            ├── Scorer           (sub-agent)
            └── GapAnalyzer      (sub-agent)
                        └── Verifier
                                └── Storage
                                        └── Dashboard (read-only)
```

### Component Responsibilities

| Component | Responsibility | Stop Condition |
|---|---|---|
| **Trigger** | Initiates the scheduled run | Fires once per schedule interval |
| **Orchestrator** | Reads state, delegates to sub-agents, manages flow | All sub-agents have completed or a sub-agent has faulted |
| **Fetcher** | Fetches live job listings from external sources | All configured sources have been queried |
| **Scorer** | Scores each listing for fit against the user profile | All fetched listings have a score |
| **GapAnalyzer** | Identifies skill gaps between user profile and target roles | All scored listings have a gap analysis |
| **Verifier** | Validates output quality and consistency across pipeline stages | All pipeline outputs pass verification checks |
| **Storage** | Persists verified output | Verified data is written to the store |
| **Dashboard** | Reads from Storage and renders the UI | Read-only; never writes |

### Hard Architectural Rules

- The **Orchestrator reads state and delegates**. It never directly fetches job listings, scores listings, or performs gap analysis.
- Each **sub-agent has exactly one goal and one stop condition** (see table above). Do not give a sub-agent responsibilities that belong to another component.
- The **Dashboard is strictly read-only**. It must never write to Storage or trigger pipeline stages.
- The **Verifier runs after all sub-agents complete**, before any data reaches Storage.
- New components or cross-component responsibilities require explicit user approval before implementation.

---

## Coding Conventions

- Language: **Python**
- Dashboard: **Streamlit**
- Each sub-agent lives in its own module/file; no cross-imports between sub-agents.
- Configuration (schedule, sources, profile path) lives in a single config file, not hardcoded.
- Secrets (API keys, credentials) must use environment variables or a `.env` file — never committed to source control.
- All pipeline stages must emit structured logs so the Verifier and Orchestrator can inspect them.

---

## What to Flag Before Doing

Raise these with the user before implementing:

- Any change to the pipeline order or data flow
- Adding direct capabilities to the Orchestrator (fetching, scoring, analysis)
- Merging two sub-agents into one, or splitting one into multiple
- Giving the Dashboard any write path to Storage
- Introducing a new external dependency not already in the project


---

## Network & Sources

9. **Every external source lives behind a `Source` class with a uniform interface.**
   The Fetcher iterates over registered Source instances; it never contains
   source-specific parsing logic. Adding a new source means adding a new Source
   class — the Fetcher itself must not need editing.

10. **Every Source returns a list of normalised dicts with exactly these keys:**
    `source`, `external_id`, `title`, `company`, `location`, `url`,
    `description`, `posted_at`, `raw`.
    Missing values must be `None` — never empty string, never `"N/A"`.

11. **All network calls go through one shared helper.**
    That helper enforces a 10-second timeout (configurable), 2 retry attempts
    with exponential backoff, and a descriptive `User-Agent` header.
    Bare `requests.get()` calls are not permitted anywhere else in the codebase.

12. **A source failing must never kill the cycle.**
    Wrap each source's fetch in a per-source try/except. Log the failure to
    `cycle_log` with `status="failed"` and a clear error message, then continue
    to the next source. One dead job board must not prevent other sources from
    running.

13. **Secrets come from environment variables loaded from a `.env` file.**
    The `.env` file is gitignored. No API key or credential may appear as a
    literal in code or in `config.yaml`. If a required key is absent at runtime,
    the source must skip itself and emit a clear log line — it must not raise an
    exception or crash the cycle.

14. **Respect the source.**
    Rate-limit to at most 1 request per second per source. Set a real,
    descriptive `User-Agent` string. Honour any documented page limits or
    `Retry-After` headers. Do not hammer an API and do not misrepresent the
    client.
