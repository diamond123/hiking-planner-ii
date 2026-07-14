# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hiking Planner: a chat app that acts as a hiking-planning assistant for the San Francisco Bay Area. A
LangGraph agent (backend) does retrieval + condition-checking + generation; an HTML/CSS/JS frontend built
with Vite talks to it over a streaming HTTP endpoint. Full behavioral spec is in
`hiking_planner_specification.md` at the repo root — read it before changing agent behavior, as most
design decisions (guardrails, retry limits, geo filtering, etc.) trace back to specific numbered
requirements there. Note the spec originally called for a plain no-build frontend; the frontend now
requires Vite (see below) — a deliberate departure from that requirement, not an oversight.

## Commands

Backend (from `backend/`, uv-managed):
```bash
uv sync                                              # install/sync deps from pyproject.toml / uv.lock
uv run uvicorn app.main:app --reload --port 8000     # run the API server
uv run python scripts/verify_qdrant.py               # sanity-check embedding model + geo filter against
                                                       # the local Qdrant store (see caveat below)
```

Frontend (from `frontend/`, **Vite is required**, not optional):
```bash
npm install
npm run dev      # vite dev server on 127.0.0.1:5500, reads frontend/.env for API_KEY/API_URL
npm run build     # vite build -> frontend/dist/, then vite preview on 127.0.0.1:5500
```
Then open `http://localhost:5500`. The frontend calls the backend at `API_URL` from `frontend/.env`
(defaults to `http://localhost:8000/api/chat` in `app.js` if unset), so both must be running.

**`frontend/.env` must exist with `API_KEY` and `API_URL`** — `API_KEY` must match `BACKEND_API_KEY` in
`backend/.env`. `vite.config.js` injects these into `import.meta.env.API_KEY`/`API_URL` at build/dev time
via its `define` block; this only happens through Vite's transform. Serving `frontend/` with a plain
static file server (e.g. `python3 -m http.server`) will **not** work anymore — `import.meta.env` won't be
populated, and the app will show a "Configuration error" on load. `index.html` loads `app.js` as
`type="module"` for this reason.

There is no test suite, linter, or CI config in this repo currently.

### Local Qdrant lock caveat

`backend/qdrant_data/` is a **local, file-based Qdrant store** (not a server), opened via
`QdrantClient(path=...)` and holding an exclusive on-disk lock. Only **one process** can have it open at a
time. Concretely:
- Never run `scripts/verify_qdrant.py` while `uvicorn` is running (or vice versa) — the second one will
  fail with `RuntimeError: Storage folder ... is already accessed by another instance`.
- `app/qdrant_store.py` opens this client once as a module-level singleton at import time — don't
  instantiate a second `QdrantClient(path=...)` anywhere.

## Architecture

### Backend (`backend/app/`) — FastAPI + LangGraph agent

Three POST endpoints, all gated by an `X-API-Key` header checked against `BACKEND_API_KEY` in `.env` (see
`security.py`) and rate-limited via `enforce_rate_limit` (`rate_limit.py`) to `RATE_LIMIT_PER_SECOND` (5, in
`constants.py`) requests per second per client IP (`request.client.host`), sliding-window, returning `429`
with `Retry-After: 1` over the limit. Both dependencies are wired rate-limit-first, so a flood is rejected
before the (cheap) API key check or any LLM/Tavily/Qdrant cost. **The rate limiter is per-process,
in-memory** — correct for the current single uvicorn worker, but each worker would count independently if
ever scaled to multiple processes; a shared store (e.g. Redis) would be needed for a real cross-worker
limit.

- `POST /api/verify-turnstile` — body `{token}`, verifies a Cloudflare Turnstile token via
  `verify_turnstile_token()` (`turnstile.py`), which POSTs to Cloudflare's `siteverify` API with
  `TURNSTILE_SECRET_KEY` and the client IP. Returns `{"success": true}` or `403`. This is the backend half
  of the human-verification gate described in the Frontend section below — see there for the full flow.
- `POST /api/end-session` — body `{session_id}`, calls `compiled_graph.checkpointer.adelete_thread(session_id)`
  to drop that thread's checkpointed state entirely. Used by the frontend's inactivity handling (see below)
  to actually free the abandoned session's memory rather than leaving it to linger forever — `InMemorySaver`
  has no TTL/expiry of its own.
- `POST /api/chat` — body `{session_id, message}`. The response is a **streamed
NDJSON body** (`application/x-ndjson`), one JSON object per line:
- `{"type": "status", "text": "..."}` — progress updates emitted while the graph runs
- `{"type": "final", "markdown": "...", "session_id": "..."}` — always the last line of every turn
- `{"type": "error", "text": "..."}` — on unhandled exceptions (stack traces are logged server-side only)

Status events are emitted from inside graph nodes via LangGraph's `get_stream_writer()`, and consumed in
`main.py` via `compiled_graph.astream(..., stream_mode=["custom", "values"])` — the `"custom"` channel
yields the status dicts nodes write, the `"values"` channel yields full-state snapshots (used to pull the
final state after the stream ends). This dual-mode pattern is the key mechanism tying node-level progress
to what the frontend displays; if you add a new long-running node, emit a `writer({"type": "status", ...})`
at its start.

**Conversation state** (`state.py`, `HikingState` TypedDict) is checkpointed per-session via LangGraph's
`InMemorySaver`, keyed by `thread_id = session_id` (generated client-side, see frontend below). `reset_turn`
is the graph's entry point, so it runs at the start of **every** `/api/chat` call, not just after a plan
completes. State splits into two intended lifetimes:
- **Persists across turns** (slot-filling): `hiking_date`, `location_text`, `location_latlon`,
  `preferences_text`, `preferences_asked`, `preferences_ask_count`, `known_preference_topics`,
  `missing_preference_topics`, `request_start_index`.
- **Reset every turn** by `reset_turn`: `attempt_count`, `excluded_sources`, `candidate_chunk`,
  `candidate_document`, `weather_result`, `trail_result`, `final_markdown`, `route_signal`,
  `date_rejection_reason`. The trail-retry loop (see below) is intra-turn — it does not span multiple HTTP
  requests.

**Starting a new planning request after a completed plan**: `reset_turn` checks `state.get("final_markdown")`
*before* clearing it — if truthy, the prior turn just finished a plan, so `reset_turn` also wipes the
slot-filling fields above (`hiking_date`, `location_text`, `location_latlon`, `preferences_text`,
`preferences_asked`, `preferences_ask_count`, `known_preference_topics`, `missing_preference_topics`) and
sets `request_start_index` to the index of the just-appended new message. This is necessary but was **not
sufficient on its own** the first time it was implemented: `extract_slots`'s `slot_extractor_llm` call reads
the message history to (re)extract slots, and the completed request's messages (e.g. "hiking on 2026-07-25
near Fremont") are still sitting in `messages` forever — clearing the state fields didn't stop the LLM from
just re-deriving the same values by reading those old messages again, so a bare "plan me another hike" was
observed silently regenerating the *same* prior plan with zero questions asked. The actual fix is
`request_start_index`: `extract_slots` slices `state["messages"][request_start_index:]` before handing
messages to `slot_extractor_llm`, so a completed request's messages are structurally excluded from
extraction for the next one, not just hoped-away via cleared state. Defaults to `0` for a session's first
request (extract from the whole history, as before).

Conversations are **in-memory only** — they're lost on backend restart. There is no persistent chat
history store.

**Graph topology** (`graph.py` wires `nodes.py` + `routing.py`): every node function takes `HikingState`
and returns a partial-state update dict; routing functions inspect the merged state to pick the next node.
Weather is checked once per turn, up front, using the user's stated area (not a specific candidate trail);
trail-specific search and condition checks happen after. Flow, in order:

1. `reset_turn` → `guardrail` — keyword blocklist (`GUARDRAIL_KEYWORDS` in `prompts.py`, catches literal
   "system prompt" / "ignore instructions" style attempts per spec) short-circuits before the LLM call;
   otherwise an LLM classifier (`guardrail_llm`, structured output `GuardrailVerdict`) judges on-topic-ness
   and injection intent. Blocked → polite refusal, `route_signal="off_topic"`, graph ends (`END`).
2. `extract_slots` — if every slot is already fully known (date, location, preferences with no
   `missing_preference_topics`), skips the LLM call entirely as a cost optimization. Otherwise calls
   `slot_extractor_llm` (structured output `ExtractedSlots`) over `messages[request_start_index:]` — i.e.
   the current planning request's messages only, not the whole session (see the `request_start_index` note
   above) — with a system prompt dynamically extended to include **today's date in `America/Los_Angeles`**
   so relative dates ("tomorrow", "this Saturday") resolve to absolute `YYYY-MM-DD`. Merges with prior
   known values.
   **Known sharp edge**: `EXTRACT_SLOTS_SYSTEM_PROMPT` has an explicit, hard-won instruction that
   `preferences_text` must stay `null` unless the assistant already asked about preferences earlier in the
   conversation — earlier versions caused the LLM to silently invent `"no specific preference"` on the
   very first turn, skipping the preferences question entirely. Re-verify this case if you touch that
   prompt.
   - **Location filler-word stripping**: `_strip_location_filler()` removes leading relational words
     ("near", "close to", "around", "by", "next to", "in the vicinity of", "in") from `location_text` via
     regex before anything else touches it. This exists because Nominatim geocoding fails on phrases
     containing them (`geocode_location("close to san jose, ...")` → no results, but `geocode_location("san
     jose, ...")` resolves fine) — the LLM extractor was including these filler words verbatim because the
     `ExtractedSlots.location_text` field description's own example used to read `'near Mount Diablo'`,
     literally modeling the bug. Both the field description and `EXTRACT_SLOTS_SYSTEM_PROMPT` now instruct
     the LLM to strip filler words itself, but `_strip_location_filler()` is a deterministic backstop, not
     solely dependent on prompt compliance — apply it to any new place where `location_text` is produced.
   - **Location scope check**: if `location_text` is newly present, `_normalize_or_reject_location()`
     rejects it (`route_signal="off_topic"`, `LOCATION_SCOPE_REJECTION_MESSAGE`, ends turn) if it names a
     non-CA US state or non-US country (lists in `constants.py`); otherwise normalizes it (appends
     ", California"/", USA" if absent) before geocoding via Nominatim (`geocode.py`) into
     `location_latlon`, to improve geocoding precision and keep the assistant scoped to the Bay Area.
     `geocode_location()` retries once (`GEOCODE_MAX_ATTEMPTS`, 1s backoff) before giving up, since
     Nominatim is a free, shared, rate-limited public service that occasionally times out or throttles —
     without a retry, a purely transient blip on a perfectly valid city (e.g. "Fremont") would surface to
     the user as `ASK_LOCATION_CLARIFICATION_MESSAGE` ("couldn't confidently place that location") even
     though nothing was wrong with the input. Note this is also naturally self-healing across turns even
     without the retry: `location_text` persists once normalized, and `extract_slots` re-attempts
     `geocode_location()` on *every* turn where `location_latlon` is still `None` — so the very next user
     message (even an unrelated one) retries the geocode call again.
   - **Preference-topic tracking**: `_extract_known_preference_topics()` regexes `preferences_text`
     against `PREFERENCE_TOPICS` (`views`/`difficulty`/`elevation_gain`/`distance` keyword lists in
     `constants.py`) to compute `missing_preference_topics`, ordered by `PREFERENCE_TOPIC_ORDER`
     (`["distance", "views", "difficulty", "elevation_gain"]`, in `constants.py`) rather than alphabetically
     — a deliberate product choice for which order `ask_preferences` asks about missing topics in, not an
     incidental one. `_is_no_preference()` recognizes several "no preference" phrasings as a substring match
     to short-circuit further asking.
   - **Preference realism check**: whenever `preferences_text` changes to genuinely new, non-"no
     preference" content, `preference_realism_llm` (structured output `PreferenceRealismVerdict`,
     `PREFERENCE_REALISM_SYSTEM_PROMPT`) judges whether it's physically achievable for a single-day Bay
     Area hike (calibrated to ~20-25 miles / ~5,000-6,000 ft gain as the realistic upper bound), defaulting
     to realistic when reasonable or ambiguous. Unrealistic → `RIDICULOUS_PREFERENCE_MESSAGE` ("Are you
     serious? I cannot find a hiking place for that. Could you give me a more realistic preference?"),
     `route_signal="off_topic"` (reusing the same hard-stop-this-turn signal as the location scope check —
     it doesn't literally mean off-topic, just "end the turn now"), and `preferences_text` is *not*
     persisted, so the rejected preference is forgotten rather than stuck permanently — the user can simply
     state a different one next turn, and `hiking_date`/`location_text` (already-known slots) are preserved
     in the same return so they don't need to be re-given, mirroring the date-rejection recovery flow. Only
     runs when `preferences_text` actually changed from its prior value (not on every turn), to avoid
     re-judging already-accepted preferences on every subsequent slot-filling turn.
   - **Date realism check**: `_validate_hike_date()` rejects a resolved `YYYY-MM-DD` `hiking_date` if it's
     in the past, more than `MAX_DATE_DAYS_AHEAD` (365, in `constants.py`) days out, or is *today* but the
     current Pacific-time hour is past `SAME_DAY_CUTOFF_HOUR` (16, i.e. 4pm — not enough daylight left to
     start a hike). A rejected date is cleared back to `None` (so routing treats it exactly like a
     never-given date) and the reason is stashed in `date_rejection_reason`, which `ask_date` reads to
     produce a tailored "that date won't quite work — {reason}" message instead of the generic first-ask
     one (`ASK_DATE_AGAIN_TEMPLATE` vs `ASK_DATE_MESSAGE` in `prompts.py`). Unparseable/unresolved date
     strings are left alone (not rejected) — this only fires once the extractor has resolved an actual
     calendar date.
3. Routing (`route_after_extract_slots`) — `route_signal=="off_topic"` (location rejected) → end turn; no
   date → `ask_date`; location given but not geocoded → `ask_location_clarification`; preferences not
   declined and topics still missing and `preferences_ask_count < MAX_PREFERENCE_ASKS` (3, in
   `constants.py`) → `ask_preferences`; otherwise → `check_weather`.
   - `ask_preferences` composes a single targeted question from whatever's actually still missing (date/
     location if somehow still unset, plus specific missing topics via `PREFERENCE_TOPIC_LABELS`) and
     increments `preferences_ask_count` — so up to `MAX_PREFERENCE_ASKS` rounds of narrowing questions,
     not just one generic ask.
4. `check_weather` — calls the **NWS (National Weather Service) API** directly (`_get_nws_forecast` in
   `tools.py`: `api.weather.gov/points/{lat},{lon}` for grid metadata, then the grid's forecast endpoint,
   matching the period whose date equals `hiking_date`) — **not Tavily** for weather anymore. Uses
   `location_latlon` from the user's stated area, falling back to `BAY_AREA_FALLBACK_LATLON` (downtown SF)
   if ungeocoded. If NWS returns nothing, treats weather as `ok=True` without an LLM call; otherwise
   `condition_judge_llm` (structured output `ConditionJudgment`, defaults `ok=True` on inconclusive
   evidence — see `WEATHER_JUDGE_SYSTEM_PROMPT`) judges go/no-go. Bad → `weather_bad_response` (asks user
   to pick another date, ends turn — does **not** consume a retry attempt); good → `search_qdrant`.
5. `search_qdrant` — embeds the query (prefs + location text) with `text-embedding-3-small` (confirmed via
   `scripts/verify_qdrant.py` as the correct model for the existing vectors — `ada-002` gives near-random
   results on this store), queries Qdrant with `limit=1`, a `must_not` filter excluding `metadata.source`
   values already tried this turn (`excluded_sources`), and — if `location_latlon` is known — a native
   `geo_radius` filter (50 miles). No payload index exists or is needed: this is a local on-disk Qdrant
   store where `create_payload_index` is a no-op, but `geo_radius` filtering still works correctly via
   brute force at this data scale (~5k points). No results → `no_candidates_response` (apology, ends turn).
   As soon as a candidate is found, its `metadata.source` is appended to `excluded_sources` and returned in
   the same update — this is the single place that owns exclusion, so a candidate can never be reselected
   within the same turn's retry loop regardless of why it's later rejected.
6. `check_trail` — Tavily search (`tools.py`, query now also includes `hiking_date`) for the candidate
   chunk's trail conditions, judged by `condition_judge_llm` (`TRAIL_JUDGE_SYSTEM_PROMPT`). Bad → loops
   back to `search_qdrant` (if `attempt_count < MAX_ATTEMPTS`, 4, in `constants.py`) or routes to
   `exhausted_response` (apology, ends turn); good → `fetch_document`.
7. `fetch_document` — looks up the full document text from `backend/qdrant_data/documents.db` (a separate
   sqlite3 db, table `documents(source PK, content, metadata, ...)`) by `candidate_chunk.metadata.source`.
8. `generate_plan` — feeds the sqlite document content + weather/trail summaries to `plan_writer_llm`
   (gpt-4o-mini, higher temperature) to produce the final markdown plan (summary, trail sequence, weather,
   trail conditions sections — see `GENERATE_PLAN_SYSTEM_PROMPT`). Sets `final_markdown`, ends turn.

All "ask the user something and stop" points use plain `END` routing rather than LangGraph's
`interrupt()` — correct here because each `/api/chat` HTTP call already represents a resumed turn via the
checkpointer, so there's no need for mid-node human-in-the-loop pausing.

**Module map**: `config.py` (pydantic-settings from `.env`, also calls `load_dotenv()` to populate
`os.environ` for libraries that read env vars directly — LangSmith tracing, the Tavily wrapper),
`constants.py` (tunable limits — `MAX_ATTEMPTS`, `MAX_PREFERENCE_ASKS`, `BAY_AREA_FALLBACK_LATLON` — plus
the preference-topic and out-of-scope-location keyword tables), `llm.py` (singleton `ChatOpenAI` instances
incl. structured-output binds), `qdrant_store.py` / `geocode.py` / `db.py` / `tools.py` (external system
access, one module each — `tools.py` now holds both the NWS weather client and the Tavily trail-conditions
search), `schemas.py` (all Pydantic structured-output models), `prompts.py` (every system prompt / template
as a constant — this is the file to edit when tuning agent behavior).

LangSmith tracing needs no separate SDK wiring — it activates purely from the `LANGSMITH_TRACING` /
`LANGSMITH_PROJECT` / `LANGSMITH_API_KEY` env vars being present at import time (the `langsmith` package
comes transitively via `langchain`/`langgraph`).

### Frontend (`frontend/`) — HTML/CSS/JS built with Vite

`index.html` + `style.css` + `app.js` (loaded as an ES module), plus `marked.js` loaded from a CDN
`<script>` tag for markdown rendering (`marked.parse(...)`). Vite (`vite.config.js`, `package.json`) is now
a required dev/build dependency — see the Commands section above for why. `frontend/dist/` is committed
build output from a prior `npm run build`; regenerate it with that command if `app.js`/`index.html` change
and the built artifact matters.

`app.js` key mechanics:
- `API_KEY`/`API_URL`/`TURNSTILE_SITE_KEY` are read from `import.meta.env.*` (populated by Vite's `define`
  config from `frontend/.env`) — no longer hardcoded in source. `API_KEY` must match `BACKEND_API_KEY` in
  `backend/.env`; this is still a basic request-origin gate, not a real secret boundary, since the built
  bundle embeds the value as a literal. `runApp()` shows a "Configuration error" bubble if `API_KEY`
  resolves empty (e.g. `frontend/.env` missing or Vite not used to serve the page).
- `session_id` is a `crypto.randomUUID()` generated once and stored in `sessionStorage` (so it survives
  page reloads within a tab but not across tabs/restarts) — this is the `thread_id` the backend checkpoints
  conversation state under.
- Uses `fetch()` + `response.body.getReader()` to read the streamed NDJSON response line-by-line, **not**
  `EventSource` (which can't send custom headers or a POST body, and this app needs both for the API key
  and message payload). Lines are parsed as JSON and dispatched by `type` (`status` updates the status
  line; `final` renders a new assistant bubble, markdown via `marked.parse` if present; `error` renders a
  plain-text bubble).

**Human-verification gate (Cloudflare Turnstile)**: `index.html` has a `#turnstile-gate` overlay shown on
load, with the main `#app` chat UI starting `hidden`. The gate's own script tag —
`<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoad" defer>` — is
the official Cloudflare-hosted script (no npm package; `@microsite/turnstile`, floated at one point, doesn't
exist on the npm registry). It's listed in `index.html` *after* the `app.js` module script so that
`window.onTurnstileLoad` (assigned at `app.js` module-evaluation time) is guaranteed to exist before
Cloudflare's script invokes it — both deferred/module scripts execute in relative document order, so this
ordering is load-bearing, don't reorder those two `<script>` tags.

Flow: `onTurnstileLoad()` either skips straight to `unlockApp()` if `sessionStorage.human_verified` is
already `"true"` (so a reload in the same tab doesn't re-challenge), or renders the widget via
`turnstile.render(...)` with `TURNSTILE_SITE_KEY`. Its success callback (`verifyTurnstileToken`) POSTs the
token to `POST /api/verify-turnstile` (with the same `X-API-Key` header as chat requests); on `{success:
true}` it sets the `sessionStorage` flag and calls `unlockApp()` (hides the gate, shows `#app`, calls
`runApp()`). This is a one-time-per-session gate, not per-message — Turnstile tokens are single-use/
short-lived, so re-verifying on every chat message isn't the model here.

**CSS gotcha already hit once, worth knowing before touching `.app`/`.gate` visibility**: the `hidden`
attribute's default UA style (`[hidden] { display: none }`) has the *same specificity* as a class selector
like `.app { display: flex }` — author stylesheets win specificity ties over the UA stylesheet, so setting
`el.hidden = true` in JS silently does nothing if the element's class already sets its own `display`. Fixed
via an explicit `.app[hidden], .gate[hidden] { display: none }` rule in `style.css`. If you add another
toggled section styled with its own `display`, it needs the same `[hidden]` override or it'll render
regardless of the `hidden` property.

**Inactivity handling**: purely client-side (`app.js`) — there's no server-push channel (no WebSocket/SSE),
so the backend can never proactively message the user; only the frontend can notice idle time via timers.
`scheduleInactivityNudge()` is called every time an assistant response lands (`handleEvent`, on `"final"`/
`"error"`) and cancelled (`clearInactivityTimer()`) the moment the user sends anything (`sendMessage()`), so
only genuine silence *after* an assistant message accumulates. Two-stage timeout: after
`INACTIVITY_NUDGE_MS` (2 min) of silence, `showInactivityNudge()` appends an italicized "Are you still
there?" bubble — repeating the last assistant message verbatim if it's short (`<= REPEAT_QUESTION_MAX_LENGTH`,
220 chars, so real questions get repeated but full markdown plans don't dump a wall of text back at the
user) — then arms a second timer. After another `INACTIVITY_END_MS` (2 min, 4 min total) of continued
silence, `endSessionDueToInactivity()` shows a goodbye message, disables the input, best-effort POSTs to
`/api/end-session` to drop the backend's checkpointed state for that thread, then calls `startNewSession()`.

**Session identity reset vs. message history clearing are deliberately decoupled**, driven by two separate
timers:
- `startNewSession()` runs after `SESSION_RESET_DELAY_MS` (4s, not immediately — an earlier version cleared
  `messagesEl.innerHTML` in the same tick the goodbye message was appended, so it never actually rendered
  before vanishing). It drops `sessionStorage.session_id`, mints a new one, and re-enables the input — so
  the same tab can start a new conversation right away without a page reload. It does **not** touch
  `messagesEl`.
- The old conversation's messages (including the goodbye bubble) stay on screen for up to
  `HISTORY_CLEAR_DELAY_MS` (20 min) after that, in case the user comes back and wants to see them —
  `startNewSession()` arms `historyClearTimer` for this. Whichever comes first wins: either the 20 minutes
  elapse and `clearMessageHistory()` fires on its own, or the user sends a new message first, in which case
  `sendMessage()` checks for a pending `historyClearTimer` and calls `clearMessageHistory()` immediately —
  so a returning user's new conversation never visually blends with the old one, but also isn't forced to
  wait out the full 20 minutes to start it.

Verified with Playwright's `page.clock` (fake timers) rather than waiting out real minutes — if you touch
this, install the fake clock *before* triggering the action whose `setTimeout` you want to control, not
after; a clock installed after a real timer is already pending won't affect that timer.

## Environment variables

`backend/.env`: `OPENAI_API_KEY`, `TAVILY_API_KEY`, `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` /
`LANGSMITH_API_KEY`, `QDRANT_PATH` (relative to `backend/`), `QDRANT_COLLECTION_NAME`, `BACKEND_API_KEY`
(validates frontend requests — must match `API_KEY` below), `TURNSTILE_SECRET_KEY` (Cloudflare Turnstile
secret, used server-side against the `siteverify` API — currently set to Cloudflare's "always passes"
testing secret `1x0000000000000000000000000000000AA`; swap for a real secret before any real deployment).

`frontend/.env`: `API_KEY` (must match `BACKEND_API_KEY` above), `API_URL` (backend chat endpoint, e.g.
`http://localhost:8000/api/chat`), `TURNSTILE_SITE_KEY` (Cloudflare Turnstile site key for the widget —
currently Cloudflare's testing sitekey `1x00000000000000000000AA`, which always passes and shows a visible
"for testing only" watermark; swap for a real site key before any real deployment). Read by
`vite.config.js` at build/dev time, not by the browser directly.
