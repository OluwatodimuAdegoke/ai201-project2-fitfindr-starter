# FitFindr 🛍️

A multi-tool AI agent for thrifting. Describe what you want in plain language and FitFindr
searches a secondhand-listings dataset, suggests how to wear the top find with your existing
wardrobe, and writes a short, shareable "fit card" caption — while handling the messy cases
where a tool returns nothing or an LLM call fails.

Built on Groq (`llama-3.3-70b-versatile`) with a Gradio UI.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Mac/Linux  (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
```

Create a `.env` file in the repo root (already git-ignored — never commit it):

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com) — no credit card required.

### Run it

```bash
python agent.py     # CLI: runs the happy path + the no-results path
python app.py        # Gradio UI (open the localhost URL printed in your terminal)
pytest tests/        # unit tests for the tools + query parser
```

---

## Tool Inventory

All three tools live in `tools.py` and can be called and tested in isolation.

### 1. `search_listings(description, size, max_price) → list[dict]`
- **Inputs:**
  - `description` (`str`) — free-text keywords, e.g. `"vintage graphic tee"`.
  - `size` (`str | None`) — size filter, or `None` to skip. Case-insensitive **substring** match, so `"M"` matches listing sizes `"M"`, `"S/M"`, and `"M/L"`.
  - `max_price` (`float | None`) — inclusive price ceiling, or `None` to skip.
- **Output:** a `list[dict]` of full listing records (`id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted by relevance, best match first. Empty list if nothing matches.
- **Purpose:** the only non-LLM tool. Applies the size/price hard filters, then scores each surviving listing by weighted keyword overlap with `description` (title & style tags weighted highest), drops zero-score listings, and ranks the rest.

### 2. `suggest_outfit(new_item, wardrobe) → str`
- **Inputs:**
  - `new_item` (`dict`) — the selected listing (the agent passes the top search result).
  - `wardrobe` (`dict`) — a wardrobe with an `"items"` list; each item has `name`, `category`, `colors`, `style_tags`, optional `notes`. May be empty.
- **Output:** a non-empty `str` of 1–2 outfit suggestions plus a styling tip.
- **Purpose:** asks the LLM to combine the new item with **named** pieces from the user's wardrobe. With an empty wardrobe it switches to general styling advice instead of inventing owned pieces.

### 3. `create_fit_card(outfit, new_item) → str`
- **Inputs:**
  - `outfit` (`str`) — the suggestion text from `suggest_outfit`.
  - `new_item` (`dict`) — the selected listing (used for the item name, price, and platform).
- **Output:** a 2–4 sentence `str` caption — casual and shareable, mentioning the item name, price, and platform once each.
- **Purpose:** writes the "OOTD post" caption. Runs at **temperature 0.9** so the output differs between runs and between different inputs.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` is a linear loop whose branches are driven by what
each tool returns — it does **not** call all three tools unconditionally:

1. **Initialize** a fresh `session` dict (the single source of truth for the interaction).
2. **Parse** the query with regex/string rules — no LLM call — into `description`, `size`, and
   `max_price` (e.g. `"under $30"` → `max_price=30.0`, `"size M"` → `size="M"`). Stored in
   `session["parsed"]`.
3. **Search (with a retry loop):** call `search_listings(**parsed)`.
   - **Retry branch:** if the result is `[]` and a loosenable filter is still active, the agent
     reacts by dropping one constraint and searching again — first the **size** filter, then the
     **price ceiling** — recording each change in `session["adjustments"]`. It keeps trying until
     results appear or all filters are exhausted. This is the input-driven part of the loop: the
     agent changes its own parameters in response to what came back, rather than running a fixed
     pass. If a retry succeeds, `session["notice"]` explains what was loosened.
   - **Error path:** if results are *still* `[]` after loosening, set `session["error"]` to an
     actionable message and **return immediately**. `suggest_outfit` and `create_fit_card` are
     never called, and `fit_card` stays `None`.
4. **Select** the top-ranked listing → `session["selected_item"]`.
5. **Suggest** an outfit from the selected item + wardrobe → `session["outfit_suggestion"]`.
6. **Fit card** from the outfit + selected item → `session["fit_card"]`.
7. **Return** the completed session.

Behavior visibly differs by input: an impossible query terminates after one tool call; a
matching query runs the full three-tool chain.

---

## State Management

A single `session` dict (built by `_new_session` in `agent.py`) is passed through the loop.
Each step reads its inputs from `session` and writes its output back — nothing is re-entered by
the user, and no hardcoded values bridge the steps.

| Field | Set by | Consumed by |
|-------|--------|-------------|
| `query` | caller | parse step |
| `parsed` (`description` / `size` / `max_price`) | parse step | `search_listings` |
| `search_results` | `search_listings` | select step / empty-check |
| `adjustments` (list[str]) | retry loop | error message / `notice` |
| `notice` (str \| None) | retry loop | UI (shown above the listing) |
| `selected_item` | select step | `suggest_outfit` **and** `create_fit_card` |
| `wardrobe` | caller | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI / caller |
| `error` | any step that fails | UI / caller (checked first) |

The exact dict in `session["selected_item"]` is the same object handed to both LLM tools, so the
item found by `search_listings` flows into `suggest_outfit` automatically. `app.handle_query`
reads `selected_item`, `outfit_suggestion`, and `fit_card` (or `error`) off the returned session
to populate the three UI panels.

---

## Error Handling Strategy

Each tool owns its failure mode — none fail silently and none crash the agent.

| Tool | Failure mode | Response |
|------|-------------|----------|
| `search_listings` | No listings match | Returns `[]` (never raises). The loop sets `session["error"]` with what was searched and how to loosen it, then stops before the next tools. |
| `suggest_outfit` | Empty wardrobe **/** LLM error | Empty wardrobe routes to a general-advice prompt (fallback strategy). Any LLM exception is caught and a non-empty fallback suggestion is returned. |
| `create_fit_card` | Missing/empty outfit **/** LLM error | Empty `outfit` returns a descriptive guard string. Any LLM exception is caught and a fallback caption is returned. |

**Concrete example (from a real run of `python agent.py`):**

Query `"designer ballgown size XXS under $5"` → `search_listings` returns `[]`, and the agent
responds without ever calling the downstream tools:

```
No listings matched 'designer ballgown' size XXS under $5.
Try raising your price, dropping the size filter, or using broader keywords.
```

By contrast, `"looking for a vintage graphic tee under $30"` returned the *Vintage Graphic Hoodie*,
an outfit naming wardrobe pieces (baggy straight-leg jeans, black combat boots), and a fit card
caption mentioning the item, `$26`, and `depop` — the full three-tool chain.

---

## Stretch Features

**Retry logic with fallback (implemented).** When `search_listings` returns nothing, the planning
loop automatically retries with progressively loosened constraints — first dropping the size
filter, then the price ceiling — and tells the user what it changed via `session["notice"]`
(e.g. *"No exact matches, so I removed the size filter and ignored the price limit to find
these."*). It only falls back to the error path if even the fully loosened search is empty. This
is documented in `planning.md` under **Stretch Features** and turns the search step into a genuine
input-driven loop. Example trace (`python agent.py`):

```
[Step 3] search_listings(size='4XL', max_price=1.0)  -> 0 result(s).
   [RETRY] removed the size filter   -> 0 result(s).
   [RETRY] ignored the price limit   -> 30 result(s).
   NOTICE: No exact matches, so I removed the size filter and ignored the price limit to find these.
```

---

## Spec Reflection

- **One way the spec helped:** writing the tool signatures, return shapes, and failure modes in
  `planning.md` *before* coding meant the planning loop's error branch (return early on empty
  search) was a deliberate, pre-decided design instead of something patched in later. The
  state-management table made wiring the `session` dict mechanical.
- **One way the implementation diverged:** the spec left query parsing open (regex *or* LLM). I
  chose **regex/string rules** instead of an LLM parse — it's deterministic, adds no API latency,
  and is trivially unit-testable. The trade-off is that very unusual phrasings could parse
  imperfectly, which is acceptable for this dataset and keeps the search step fast and free.

---

## AI Usage

> _(Edit this section to match what you actually directed the AI to do.)_

1. **Implementing `search_listings`** — I gave the AI the Tool 1 spec block from `planning.md`
   (parameters, return shape, failure mode) plus the field list from `data_loader.load_listings()`
   and asked it to implement the function using `load_listings()` rather than re-reading the file.
   I reviewed the generated code to confirm it filtered by all three parameters, did
   *case-insensitive substring* size matching (so `"M"` matches `"S/M"`), dropped zero-score
   listings, and returned `[]` for the impossible query — then verified with the three test
   queries before trusting it.

2. **Implementing the planning loop in `agent.py`** — I gave the AI the architecture diagram and
   the Planning Loop + State Management sections from `planning.md` and asked it to implement
   `run_agent()`. Before accepting it, I checked that it branched on the `search_listings` result
   and returned early on `[]` *before* calling the other tools, and that every value was written
   into the `session` dict rather than passed as locals. I confirmed both paths by running
   `python agent.py` (happy path populated all fields; the no-results path set `error` and left
   `fit_card` as `None`).
