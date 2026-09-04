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

---

## Intelligence & Scoring

15. **All LLM calls go through one module: `edgedash/llm.py`, exposing one function.**
    The provider and model name come from config, never hardcoded. Rate-limit to stay
    inside a free tier (default 1 request per second, max 15 per minute). No other file
    imports an LLM SDK.

16. **Never ask a model for a final score, ranking, or numeric rating.**
    The model extracts structured facts only. All scoring arithmetic is deterministic
    Python in one function. The model never sees the scoring weights.

17. **Every model response is validated against an explicit schema before use.**
    A response that fails validation is retried once, then logged as a failure for that
    listing only — it must not crash the cycle or stop the remaining listings. Never
    call `json.loads` on raw model text without a validation and repair path.

18. **Scoring is idempotent.**
    Never re-score a listing that already has a score. Select only listings
    `WHERE score IS NULL`. Cache extraction results keyed on a hash of the job
    description so the same text is never sent to the model twice.

19. **Every score carries a human-readable reason generated from the score components
    by our code — never free text written by the model.**

20. **Log the score distribution (count, min, max, mean, spread) to `cycle_log` on
    every scoring run.**
    A run where all scores fall within 10 points is a suspect run and must be logged
    as such.

21. **Cap listings scored per cycle at a configurable batch size (default 25)** so a
    cost or rate-limit blowup is structurally impossible.

---

## Aggregate Analysis

22. **Aggregate analysis is deterministic SQL and Python.** No LLM call may produce, adjust, or rank an aggregate number. A model may only SUGGEST canonical groupings for a human to approve.

23. **Skill names are canonicalised through an explicit alias map in config.yaml** that I own and can read. Never auto-merge skill names by model judgement or string similarity alone.

24. **Gap ranking is weighted by the fit score of the listing the gap came from.** A gap in a listing scored 20 is worth far less than a gap in a listing scored 85. Never rank gaps by raw frequency alone.

25. **Every gap report run writes a timestamped SNAPSHOT.** Never overwrite the previous report. Trend over time is a first-class output, not an afterthought.

26. **Every aggregate number must be traceable to the rows that produced it.** Any reported gap must be able to list the specific listing IDs it was computed from. No number appears in the dashboard that I cannot drill into.

27. **Report the sample size alongside every aggregate.** A gap computed from 3 listings and a gap computed from 90 listings must never be presented as equally reliable.
---

## Orchestration

28. **Orchestrator reads system state and decides which agents run.**
   It never runs a fixed sequence. Skipping because there is no work is success.

29. **Every delegation has an explicit goal and explicit stop conditions**
   such as max items or max duration. Limits come from Orchestrator/config,
   not from the sub-agent.

30. **Orchestrator coordinates only:**
   read state, build plan, delegate, collect results, log.
   No fetch/scoring/analysis logic inside it.

31. **Print and log the PLAN before execution:**
   agents that run, agents skipped, and the state value/reason for each decision.

32. **One agent failure must not stop the cycle.**
   Log it, continue remaining tasks, and mark cycle partial.

33. **Every cycle writes exactly one summary row containing:**
   what ran, what was skipped and why, duration per agent, and outcome.