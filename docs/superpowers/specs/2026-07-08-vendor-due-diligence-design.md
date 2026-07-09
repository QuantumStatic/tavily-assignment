# Vendor Due-Diligence Agent — Design Spec

**Date:** 2026-07-08
**Context:** Tavily FDE take-home (Option 1 — improve the starter agent into a purposeful product).
**Author:** Utkarsh Jain

---

## 1. Purpose

A **vendor due-diligence tool** for procurement teams. Give it a vendor name; get back a
**structured, cited, scored risk profile** a procurement team could file. Vendors are grouped
into **projects** so a team can compare shortlisted bidders (e.g. competing EPC contractors)
side by side and decide who to award.

**User:** a procurement engineer / analyst (often non-technical) vetting vendors before a contract award.
**Problem it solves:** vendor vetting today is manual googling — hours per vendor, unstructured,
no audit trail, and easy to attribute the wrong company's problems to your vendor.

**Why Tavily:** the product's value lives entirely in *retrieval quality* — how we query, filter,
and ground web data per risk dimension. That is exactly what Tavily is for.

---

## 2. What we discovered in the feasibility spike (drives the design)

Ran `backend/spikes/tavily_probe.py` against a public (Boeing) and a private (Cives Steel) vendor.

- **Premise holds:** legal / safety / financial dimensions return relevant, cited, substantive
  sources for both — the product spine works, and degrades gracefully on private vendors
  (still caught Cives' 2020 plant closure).
- **The core danger — entity contamination:** naive per-dimension keyword search pulled
  *other companies* into a vendor's report ("EEOC sues **U.S. Steel**", "**Bayou Steel** bankruptcy"
  under Cives Steel). For due diligence this is catastrophic, and it is the central engineering
  problem this project solves.
- **Two signals make it solvable:** real findings scored 0.5–0.9; contamination scored 0.11–0.20
  → a **score threshold** plus **entity verification** removes most of it.
- **Snippets are usable** (1.3k–2.4k chars) but noisy → Extract is a deferred enhancement, not V1.
- **Recency needs tuning** — certs/news returned stale results without a time window.

---

## 3. Architecture (data flow)

```
INPUT: vendor name
  → STAGE 0  Entity resolution (1 search) → entity card {name, domain, country, industry, parent, public?, ticker}
  → STAGE 1  Per-dimension retrieval (parallel, keyword queries, tuned params) → snippets + URLs + scores
  → FILTER   drop score < threshold; verify source names THIS entity (anti-contamination)
  → SYNTH    LLM per dimension: findings (cited) → reasoning → score(0–10)
  → ASSEMBLE sections → overall verdict (score 0–10 + reasoning)
  → CACHE    SQLite, per-section TTL
OUTPUT SURFACES: CLI · FastAPI+SSE → React · MCP check_vendor()
```

One surface-agnostic **engine**; CLI / web / MCP are thin renderers of the same structured report object.

**Deferred (documented as future work, not V1):** full-article **Extract** grounding; **adaptive
follow-up** searches (model programs extra queries when a material signal appears).

---

## 4. Retrieval strategy (the core)

Grounded in Tavily's own best-practices docs: keyword-style queries (<400 chars, "think search
query not prompt"), one concern per query, run in parallel, modest `max_results`, search-then-extract.

### Dimensions & per-dimension config

| Dimension | topic | geo anchor | exact_match | depth | max_results | recency |
|---|---|---|---|---|---|---|
| Snapshot (entity) | general | `country` | – | advanced | 5 | none |
| Legal | general | `country` | ✓ name | advanced | 5 | ~2 yr (start_date) |
| Safety / recall | general | `country` | ✓ name | advanced | 5 | ~2 yr |
| Financial (distress) | finance | keyword¹ | ✓ name | advanced | 5–8 | 1 yr (start_date) |
| Backlog / momentum | finance | keyword¹ | ✓ name | advanced | 5 | 1 yr |
| Certifications | general | `country` | – | basic | 3 | none · **include own domain** |
| News (positive) | news | keyword¹ | ✓ name | basic | 8 | **90 d (start_date)** |
| News (negative) | news | keyword¹ | ✓ name | basic | 8 | **90 d (start_date)** |

¹ `country` param is incompatible with `topic=news`/`finance`, so geo is folded into query keywords
(e.g. "Cives Steel **Georgia** ...") for those dimensions.

### Cross-cutting retrieval rules
- **exact_match** on the canonical vendor name (Tavily's named due-diligence feature) — forces the
  vendor's name to appear verbatim; blocks similarly-named companies.
- **exclude_domains** = vendor's own site (+ linkedin) for *independent* dimensions (legal/financial/news);
  **include_domains** = vendor's own site for certs (self-reported is the right source there).
- **score threshold** (~0.4) drops weak/wrong-company hits.
- **start_date** computed at runtime (`today - N days`) — `time_range` can't express 90 days.
- **Backlog signal (routed by entity card's `public?` flag):**
  - **Public vendor** → earnings-call transcript, discovered deterministically (no search credit):
    1. from entity resolution take `exchange` + `ticker`; build the Motley Fool quote URL
       `https://www.fool.com/quote/{slug}/{ticker}/` (slug: NASDAQ→`nasdaq`, NYSE/NEW YORK→`nyse`, else
       lowercased; `null` if private / non-US / missing ticker).
    2. plain `httpx` GET the quote page (free); regex the latest transcript path
       `/earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[a-z0-9-]+`; parse `callDate` from the path (→ used as `as_of`).
    3. plain `httpx` GET the transcript page + lightweight HTML→text parse (e.g. `trafilatura`);
       LLM pulls order-book/backlog commentary. **No Tavily here** — it's a single known static page,
       so a deterministic download is simpler and free. (Design principle: use Tavily for the hard
       multi-source open-web retrieval; use a plain GET for one known static source. Right tool per job,
       not integration-for-its-own-sake.)
    4. `skip=true` if no ticker / no regex match → fall back to the private path.
    (Regex-scraping is brittle to Motley Fool HTML changes → the `skip` flag degrades gracefully.
    Verify the quote + transcript pages are server-rendered with a raw GET during build, not WebFetch.)
  - **Private vendor** → aggregate project-award news as a *proxy*, labeled as an estimate.
- Execution: bounded-concurrency parallel searches, exponential backoff, dedupe by URL, one
  consistent Tavily `session_id` per report (groups the report's searches; aids tracing).

---

## 5. Synthesis & scoring

Tavily returns **structured results (snippet + url + score)** but **no per-claim citations** — binding
claims to sources is ours. We do **not** use `include_answer`.

Per dimension, one LLM call emits (evidence-first ordering):
```json
{
  "dimension": "financial",
  "findings": [{"claim": "...", "source": "url", "source_type": "independent|self_reported", "as_of": "YYYY-MM-DD"}],
  "reasoning": "why this score (hover text); flags thin coverage when data is sparse",
  "score": 0                        // 0–10; 10 = all good/confident to use, 0 = problematic
}
```
Overall verdict = same shape (score 0–10 + reasoning), a pure function of the dimension sections.

- **Score direction:** 10 = green/safe, 0 = red/avoid. Same scale for dimensions and overall.
- **Evidence-first:** findings → reasoning → score, so the score is conditioned on cited findings.
- **Honesty rule:** "no problems found" on thin coverage ≠ a confident 10 — the reasoning must
  distinguish a genuinely clean record from insufficient data (absence of evidence ≠ evidence of absence).
- Validated against a **Pydantic** schema.

---

## 6. Cache

SQLite. Cache is **per section, per vendor** (each section has its own TTL).

```sql
CREATE TABLE report_cache (
  vendor_key   TEXT NOT NULL,   -- canonical domain from entity resolution (e.g. "cives.com")
  section_type TEXT NOT NULL,   -- 'legal' | 'financial' | 'news' | ...
  content      TEXT NOT NULL,   -- JSON: the structured section
  sources      TEXT,            -- JSON: raw Tavily results used (feeds chat-RAG + audit)
  fetched_at   TIMESTAMP NOT NULL,
  PRIMARY KEY (vendor_key, section_type)
);
```

- **Primary key:** `(vendor_key, section_type)`. `vendor_key` = resolved **domain** (stable; name
  variants dedupe to one entry). Entity resolution itself is cached `normalized_name → entity_card`.
- **TTL lives in code** (policy dict), not in the row. Freshness computed on read:
  `is_fresh = (now - fetched_at) < TTL[section_type]`.
  TTLs: snapshot/certs 30d · financial/backlog 7d · legal/safety 3d · news 1d.
- **Why code-based TTL:** tunable without migrating rows; `fetched_at` doubles as the report's
  "as of" date shown in the UI.
- **Verdict is not cached** independently — recomputed from current sections (cheap), so it can never
  point at a refreshed section with a stale conclusion.
- **Second job:** the cached `sources` are the knowledge base for the chat feature.

---

## 7. Surfaces

One engine, three renderers:

- **CLI** (engineer/debug) — vendor in → report out; the fastest dev/eval loop.
- **Web (FastAPI + SSE → React)** — the non-technical end user:
  - **Project** = comparison workspace; add vendors via a search box.
  - **Comparison table** — rows = vendors, columns = dimension **scores (0–10, color-banded)**;
    sortable ascending to surface the riskiest bidder first.
  - **Hover a cell** → the LLM's **reasoning** for that score.
  - **Click a cell/row** → the **full cited report** for that dimension/vendor.
  - Sections **stream in** (SSE) as they complete; cached sections appear instantly with an
    "as of · cached" badge.
  - **Chat** over a vendor's cached data (RAG over cached sources); the chat agent can also trigger
    a **live Tavily search** for something not in cache.
- **MCP** — `check_vendor(name) → structured report`, so other agents can call due diligence as a tool.

### Data model (SQLite)
```
projects (id, name, created_at)
vendors  (id, project_id, name, vendor_key)
report_cache (vendor_key, section_type, content, sources, fetched_at)   -- §6
chats    (vendor_id, messages)
```

### Scope tiers (for when we pick what to ship)
- **Tier 1 (must):** project → add vendors → comparison table (scores/hover/expand) → cited reports.
- **Tier 2 (if time):** chat over cached data.
- **Tier 3 (showcase/future):** chat agent with live web search; Extract grounding; adaptive follow-up.

---

## 8. Evaluation & observability

- **Eval loop (both):**
  1. **Hand-verified gold set** — ~4 vendors (public w/ known issues + private), ~10 fields each,
     scored field-level precision/recall against a hand-checked sheet (the "client's answer key" method).
  2. **Automated citation-support check** — for each claim, does its cited source actually support it?
     (LLM-as-judge) — scales to any vendor, runs as a regression check.
  - Also report **contamination rate** before/after the entity-grounding layer — the headline metric.
- **Tracing:** LangSmith or OpenTelemetry spans across entity → retrieve → filter → synthesize, plus
  Tavily `session_id` correlation. (Named bonus in the assignment.)

---

## 9. Credit economics

- Full report ≈ **~13 credits** (snippets-only): entity 2 + legal 2 + safety 2 + financial 2 +
  backlog 2 + certs 1 + news ×2 (2).
- Cached repeat ≈ **~2–4 credits** (only short-TTL sections refresh).
- Extract grounding (deferred) adds ~3–6 credits (batched: 1 credit / 5 URLs basic).
- ~1,000-credit budget ≈ **~75 fresh reports** — cache aggressively during dev.
- **Business-value line:** "a cited due-diligence report costs ~13 credits (~$0.10 PAYG); the TTL
  cache cuts a repeat to ~1/4."

---

## 10. Deliverables (per assignment)

- GitHub repo (exclude `starter_agent.py` — already gitignored).
- Technical statement: the contamination problem, the retrieval strategy that solves it, the
  eval numbers, credit economics.
- Build record (coding-agent session logs / traces).

---

## Open questions / notes

- ~~Confirm `topic="finance"` quality vs `general` for financial/backlog~~ **RESOLVED (spike #2):**
  `topic="finance"` surfaces backlog-specific analysis directly — use it for financial + backlog.
  `topic="news"` + runtime 90-day `start_date` returns exactly-recent results, and news results
  carry `published_date` → use that as the `as_of` for news findings. `country` param confirmed working.
- Score threshold value (~0.4) to be tuned empirically during the eval pass.
- Model choice: start on Nebius-hosted; pin a stronger model if structured output is unreliable.
