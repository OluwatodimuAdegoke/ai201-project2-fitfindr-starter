# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Searches the 40-item mock listings dataset for secondhand pieces matching a free-text description, optionally filtered by size and a maximum price. It scores each listing by keyword overlap with the description and returns the matches ranked best-first. This is the only tool that does *not* call the LLM — it is pure Python over `load_listings()`.

**Input parameters:**
- `description` (str): Free-text keywords describing the wanted item, e.g. `"vintage graphic tee"`. Tokenized and matched against each listing's `title`, `description`, `style_tags`, `category`, `colors`, and `brand`.
- `size` (str | None): A size string to filter by, or `None` to skip size filtering. Matching is **case-insensitive substring** so `"M"` matches listing sizes `"M"`, `"S/M"`, and `"M/L"`. Listings whose size does not contain the query size are dropped.
- `max_price` (float | None): Inclusive price ceiling, or `None` to skip price filtering. Listings with `price > max_price` are dropped.

**What it returns:**
A `list[dict]`, sorted by relevance score (highest first). Each dict is a full listing with the fields: `id` (str), `title` (str), `description` (str), `category` (str), `style_tags` (list[str]), `size` (str), `condition` (str), `price` (float), `colors` (list[str]), `brand` (str | None), `platform` (str). Listings that survive the size/price filters but have a keyword-overlap score of `0` are dropped. Returns an empty list `[]` when nothing matches.

**What happens if it fails or returns nothing:**
Returns `[]` — it never raises. The agent's planning loop detects the empty list, sets `session["error"]` with a message telling the user what was searched and what to relax (e.g. "No listings matched 'designer ballgown' in size XXS under $5. Try a higher price, a different size, or broader keywords."), and returns early **without** calling `suggest_outfit` or `create_fit_card`.

---

### Tool 2: suggest_outfit

**What it does:**
Given the selected thrifted item and the user's wardrobe, asks the LLM (Groq `llama-3.3-70b-versatile`) to propose 1–2 complete, specific outfit combinations — pairing the new item with named pieces the user already owns, plus a short styling tip (how to wear/tuck/layer it).

**Input parameters:**
- `new_item` (dict): A single listing dict (the top result from `search_listings`). The prompt uses its `title`, `description`, `category`, `style_tags`, and `colors`.
- `wardrobe` (dict): A wardrobe dict with an `"items"` key holding a list of wardrobe-item dicts. Each item has `id`, `name`, `category`, `colors`, `style_tags`, and optional `notes`. The list may be empty.

**What it returns:**
A non-empty `str` of outfit suggestions in natural language. When the wardrobe has items, the suggestion names specific owned pieces (e.g. "pair with your baggy dark-wash jeans and platform Docs"). When the wardrobe is empty, it returns general styling advice for the item (what kinds of pieces pair well, what vibe it suits) instead of referencing nonexistent items.

**What happens if it fails or returns nothing:**
The empty-wardrobe case is **not** a failure — it routes to the general-advice branch and still returns useful text. If the LLM call raises (network/API error) or returns an empty/whitespace string, the function catches it and returns a plain-string fallback like `"Couldn't generate an outfit suggestion right now, but this [item] would pair well with neutral basics and your go-to denim."` — it never raises or returns `""`.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion + item details into a short, casual, shareable caption (the kind of thing you'd put under an OOTD post). Calls the LLM with a **higher temperature (~0.9)** so the output varies between runs and between different inputs.

**Input parameters:**
- `outfit` (str): The outfit suggestion string returned by `suggest_outfit`.
- `new_item` (dict): The selected listing dict — the prompt pulls `title`, `price`, and `platform` so the caption mentions each naturally, once.

**What it returns:**
A 2–4 sentence `str` usable as an Instagram/TikTok caption — casual and authentic (not a product description), mentioning the item name, price, and platform once each, and capturing the outfit's vibe in specific terms.

**What happens if it fails or returns nothing:**
First guards against an empty/whitespace-only `outfit`: returns the descriptive error string `"Can't write a fit card without an outfit suggestion — run suggest_outfit first."` (never raises). If the LLM call itself errors, it returns a plain-string fallback caption rather than propagating the exception.

---

### Additional Tools (if any)

None for the core submission. (Candidate stretch tool — `compare_price(new_item)` — would estimate whether a price is fair against comparable listings in the same `category`/`style_tags`. Will be added and documented here only if I implement the stretch feature.)

---

## Planning Loop

**How does your agent decide which tool to call next?**

`run_agent(query, wardrobe)` runs a linear loop whose branches are driven by what each tool returns, all stored in a single `session` dict:

1. **Initialize** — `session = _new_session(query, wardrobe)`.
2. **Parse** — extract `description`, `size`, `max_price` from `query` with regex/string rules (no LLM):
   - `max_price`: search for `$NN` or `under NN` / `below NN` (`re.search(r'(?:under|below|<|\$)\s*\$?(\d+(?:\.\d+)?)', query)`); else `None`.
   - `size`: search for `size <token>` or a standalone size word (`re.search(r'\bsize\s+([\w/]+)\b', query)`, plus a fallback list of `S/M/L/XL` and `US \d`); else `None`.
   - `description`: the query with the matched size/price phrases stripped out; falls back to the full query.
   - Store all three in `session["parsed"]`.
3. **Search** — call `search_listings(**session["parsed"])`; store in `session["search_results"]`.
   - **Branch (error path):** if `search_results == []`, set `session["error"]` to a helpful message naming the parsed criteria and `return session` immediately. `outfit_suggestion` and `fit_card` stay `None`. **This is the key conditional — the next two tools are not called when search is empty.**
4. **Select** — `session["selected_item"] = session["search_results"][0]` (top-ranked).
5. **Suggest** — call `suggest_outfit(session["selected_item"], session["wardrobe"])`; store in `session["outfit_suggestion"]`.
6. **Fit card** — call `create_fit_card(session["outfit_suggestion"], session["selected_item"])`; store in `session["fit_card"]`.
7. **Done** — `return session`.

The loop "knows it's done" when it reaches step 7 with no error, or when it returns early at step 3. Behavior visibly differs by input: an impossible query terminates after one tool call; a matching query runs all three.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session`) is the one source of truth for the interaction. Each tool reads its inputs from `session` and writes its output back into `session`, so nothing is re-entered by the user and no hardcoded values bridge the steps. Tracked fields:

| Field | Set by | Consumed by |
|-------|--------|-------------|
| `query` (str) | caller | parse step |
| `parsed` (dict: description/size/max_price) | parse step | `search_listings` |
| `search_results` (list[dict]) | `search_listings` | select step / empty-check |
| `selected_item` (dict) | select step | `suggest_outfit`, `create_fit_card` |
| `wardrobe` (dict) | caller | `suggest_outfit` |
| `outfit_suggestion` (str) | `suggest_outfit` | `create_fit_card` |
| `fit_card` (str) | `create_fit_card` | UI / caller |
| `error` (str \| None) | any step that fails | UI / caller (checked first) |

The exact dict in `session["selected_item"]` is the same object passed into both `suggest_outfit` and `create_fit_card` — verifiable by printing it at the end and confirming it equals `search_results[0]`. `app.handle_query` reads `selected_item`, `outfit_suggestion`, and `fit_card` (or `error`) off the returned session to fill the three UI panels.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Returns `[]` (no exception). The loop sets `session["error"]`: e.g. *"No listings matched 'designer ballgown' in size XXS under $5. Try raising your price, dropping the size filter, or using broader keywords."* and returns early — `suggest_outfit`/`create_fit_card` are never called, so the user sees actionable guidance, not a crash or an empty panel. |
| suggest_outfit | Wardrobe is empty | Not treated as an error. Routes to the general-styling-advice prompt and returns useful text (what pairs well with the item, what vibe it suits) instead of referencing pieces the user doesn't own. If the LLM itself errors, returns a plain-string fallback suggestion. |
| create_fit_card | Outfit input is missing or incomplete | Guards first: if `outfit` is empty/whitespace, returns the string *"Can't write a fit card without an outfit suggestion — run suggest_outfit first."* If the LLM call errors, returns a plain-string fallback caption. Never raises. |

---

## Architecture

```
                          User query  (+ wardrobe choice)
                                │
                                ▼
        ┌──────────────────  Planning Loop (run_agent)  ──────────────────┐
        │                                                                 │
        │   parse query (regex) ──► session["parsed"] = {desc,size,price} │
        │                                │                                │
        │                                ▼                                │
        │   search_listings(desc, size, max_price)                        │
        │        │                                                        │
        │        │ results == []                                          │
        │        ├──► session["error"] = "No listings matched…"  ─────────┼──► return early
        │        │                                                        │     (outfit_suggestion,
        │        │ results == [item, …]                                   │      fit_card stay None)
        │        ▼                                                        │
        │   session["selected_item"] = results[0]                         │
        │        │                                                        │
        │        ▼                                                        │
        │   suggest_outfit(selected_item, wardrobe) ── LLM ───┐           │
        │        │   (empty wardrobe → general advice branch) │           │
        │   session["outfit_suggestion"] = "…"  ◄─────────────┘           │
        │        │                                                        │
        │        ▼                                                        │
        │   create_fit_card(outfit_suggestion, selected_item) ── LLM ──┐  │
        │        │   (empty outfit → guard returns error string)       │  │
        │   session["fit_card"] = "…"  ◄───────────────────────────────┘  │
        │        │                                                        │
        └────────┼────────────────────────────────────────────────────────┘
                 ▼
          return session  ──►  app.handle_query maps to 3 UI panels
                                (🛍️ listing | 👗 outfit | ✨ fit card)

   session (state) is read+written by every step above — single source of truth.
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
I'll use **Claude (Claude Code)** one tool at a time.
- `search_listings`: I'll paste the Tool 1 block above (params, return shape, failure mode) plus the field list from `data_loader.load_listings()` and ask Claude to implement it using `load_listings()` — no re-reading files. **Verify before trusting:** confirm it (a) filters by all three params, (b) does case-insensitive *substring* size matching, (c) drops score-0 listings, (d) returns `[]` (never raises) for the impossible query. Test with `"vintage graphic tee"/None/50`, `"jacket"/None/10` (assert all `price<=10`), and `"designer ballgown"/"XXS"/5` (assert `== []`).
- `suggest_outfit`: I'll give Claude the Tool 2 block + the wardrobe item schema and ask for the two-branch (empty vs. populated wardrobe) implementation against Groq `llama-3.3-70b-versatile`. **Verify:** run once with `get_example_wardrobe()` (must name owned pieces) and once with `get_empty_wardrobe()` (must give general advice, not crash, not `""`).
- `create_fit_card`: I'll give Claude the Tool 3 block and ask for the empty-outfit guard + a high-temperature (~0.9) LLM call. **Verify:** run 3× on the same input and confirm captions differ; run once with `outfit=""` and confirm it returns the guard string, not an exception.

**Milestone 4 — Planning loop and state management:**
I'll give Claude the **Architecture diagram**, the **Planning Loop** section, and the **State Management** table, then ask it to implement `run_agent()` in `agent.py`. **Verify before trusting:** confirm the code (a) branches on the `search_listings` result and returns early on `[]` *before* calling the other tools, (b) writes every value into `session` rather than passing locals/hardcoded values, (c) leaves `fit_card`/`outfit_suggestion` as `None` on the error path. Then run `python agent.py` and check both the happy path (all three populated) and the no-results path (`error` set, `fit_card is None`). For `app.handle_query`, I'll give Claude the docstring TODO and have it map `session` → the three panel strings.

---

## A Complete Interaction (Step by Step)

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — Parse + Search.**
The loop parses the query → `description="vintage graphic tee"`, `size=None`, `max_price=30.0` → `session["parsed"]`. It calls `search_listings("vintage graphic tee", None, 30.0)`. Size/price filters keep listings ≤ \$30; keyword overlap (`vintage`, `graphic`, `tee` against title/description/style_tags) scores and ranks them. Returns a non-empty list, e.g. top result a faded band/graphic tee around \$22 on Depop → `session["search_results"]`.

**Step 2 — Select + Suggest outfit.**
Empty-check passes (results non-empty), so `session["selected_item"] = search_results[0]`. The loop calls `suggest_outfit(selected_item, wardrobe=get_example_wardrobe())`. The wardrobe has items, so the LLM returns specific combos referencing owned pieces, e.g. *"Wear it with your baggy dark-wash jeans and chunky sneakers; tuck the front hem for shape and add a flannel for layering."* → `session["outfit_suggestion"]`.

**Step 3 — Fit card.**
The loop calls `create_fit_card(outfit_suggestion, selected_item)` at high temperature. The LLM returns a casual caption mentioning the item name, ~\$22, and Depop once each, e.g. *"thrifted this faded graphic tee off depop for $22 and it was MADE for my baggy jeans 🖤 styled it with chunky sneakers + a flannel, full fit in my stories"* → `session["fit_card"]`. The loop returns `session` (no error).

**Final output to user:**
`app.handle_query` fills the three panels: 🛍️ the formatted top listing (title, price, condition, platform), 👗 the outfit suggestion, and ✨ the fit card. On the no-results variant (e.g. *"designer ballgown size XXS under $5"*), only the first panel shows the `session["error"]` guidance and the other two stay empty.
