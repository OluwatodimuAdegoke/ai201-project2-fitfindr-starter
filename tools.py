"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# Groq model used by the two LLM-backed tools (suggest_outfit, create_fit_card).
LLM_MODEL = "llama-3.3-70b-versatile"

# Words to ignore when scoring keyword overlap in search_listings.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "to", "of", "in", "on",
    "im", "i", "looking", "want", "need", "some", "something", "really",
    "mostly", "wear", "what", "whats", "out", "there", "how", "would",
    "style", "it", "that", "this", "my", "me", "is", "are", "size", "under",
    "below", "less", "than", "around", "about", "find", "me",
}


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _call_llm(prompt: str, temperature: float = 0.7) -> str:
    """
    Send a single user prompt to the Groq chat model and return the text reply.

    Raised exceptions are intentionally NOT caught here — each tool decides its
    own fallback behaviour so a transient API error never crashes the agent.
    """
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, and drop stopwords / tiny tokens."""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    query_tokens = _tokenize(description)

    scored: list[tuple[int, dict]] = []
    for item in listings:
        # 1. Hard filters — drop anything outside the price/size constraints.
        if max_price is not None and item["price"] > max_price:
            continue
        if size is not None and size.strip().lower() not in item["size"].lower():
            continue

        # 2. Score by weighted keyword overlap with the description.
        #    Title and style tags are the strongest relevance signals.
        title_tokens = set(_tokenize(item["title"]))
        tag_tokens = {t.lower() for t in item.get("style_tags", [])}
        desc_tokens = set(_tokenize(item["description"]))
        color_tokens = {c.lower() for c in item.get("colors", [])}
        category = item.get("category", "").lower()
        brand = (item.get("brand") or "").lower()

        score = 0
        for tok in query_tokens:
            if tok in title_tokens:
                score += 3
            if tok in tag_tokens:
                score += 3
            if tok == category:
                score += 2
            if tok in desc_tokens:
                score += 1
            if tok in color_tokens:
                score += 1
            if tok in brand:
                score += 1

        # 3. Drop listings with no keyword relevance at all.
        if score > 0:
            scored.append((score, item))

    # 4. Sort by score, highest first. Returns [] when nothing matched.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_desc = (
        f"{new_item.get('title', 'this piece')} "
        f"({new_item.get('category', 'item')}; "
        f"style: {', '.join(new_item.get('style_tags', [])) or 'n/a'}; "
        f"colors: {', '.join(new_item.get('colors', [])) or 'n/a'}). "
        f"{new_item.get('description', '')}"
    )

    items = (wardrobe or {}).get("items", [])

    if not items:
        # Empty-wardrobe branch: general styling advice, no owned pieces to name.
        prompt = (
            "You are a thoughtful personal stylist. A user is considering buying "
            f"this secondhand item:\n\n{item_desc}\n\n"
            "They haven't told you anything about their existing wardrobe. "
            "Suggest how to style this piece in general terms: what kinds of "
            "items pair well with it, what vibe/occasions it suits, and one or two "
            "concrete outfit ideas using common staples (not specific brands). "
            "Keep it to 3-5 sentences, warm and practical."
        )
    else:
        # Populated-wardrobe branch: name specific pieces the user owns.
        wardrobe_lines = "\n".join(
            f"- {it.get('name', 'item')} "
            f"({it.get('category', '')}; "
            f"{', '.join(it.get('style_tags', []))})"
            + (f" — {it['notes']}" if it.get("notes") else "")
            for it in items
        )
        prompt = (
            "You are a thoughtful personal stylist. A user is considering buying "
            f"this secondhand item:\n\n{item_desc}\n\n"
            "Here is their current wardrobe:\n"
            f"{wardrobe_lines}\n\n"
            "Suggest 1-2 complete outfits that combine the new item with SPECIFIC "
            "pieces from their wardrobe (refer to them by name). Add a short styling "
            "tip (how to tuck, layer, roll, or accessorize). Keep it to 3-6 "
            "sentences, concrete and wearable."
        )

    try:
        suggestion = _call_llm(prompt, temperature=0.7)
    except Exception:
        suggestion = ""

    if not suggestion.strip():
        # Fallback so the agent always gets usable, non-empty text.
        return (
            f"Couldn't reach the styling model just now, but "
            f"{new_item.get('title', 'this piece')} would pair well with neutral "
            "basics and your go-to denim — keep the rest of the look simple so it "
            "stands out."
        )
    return suggestion


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # 1. Guard: no outfit means there's nothing to caption.
    if not outfit or not outfit.strip():
        return "Can't write a fit card without an outfit suggestion — run suggest_outfit first."

    title = new_item.get("title", "this piece")
    price = new_item.get("price")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"
    platform = new_item.get("platform", "secondhand")

    prompt = (
        "Write a short, shareable outfit caption (2-4 sentences) for a thrifted "
        "find, like a real Instagram/TikTok OOTD post — casual and authentic, NOT "
        "a product description. Use lowercase if it feels natural and 1-2 emojis "
        "max.\n\n"
        f"Item: {title}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform}\n"
        f"Outfit / how it's styled: {outfit}\n\n"
        "Mention the item name, price, and platform naturally — each only once. "
        "Capture the specific vibe of the outfit. Make it sound genuinely posted "
        "by a person, not generated."
    )

    # 2. High temperature so the caption varies run-to-run and input-to-input.
    try:
        caption = _call_llm(prompt, temperature=0.9)
    except Exception:
        caption = ""

    if not caption.strip():
        # Fallback caption so we never return an empty string.
        return (
            f"thrifted this {title.lower()} off {platform} for {price_str} and "
            "i'm obsessed — styled it exactly how i pictured 🖤"
        )
    return caption
