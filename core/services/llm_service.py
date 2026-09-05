import json
import logging
import time

import google.generativeai as genai
from django.conf import settings

from ..analytics.events import track_vibe_generation_completed, track_vibe_generation_failed
from . import _gemini

logger = logging.getLogger(__name__)

# Brand highlight colours for the vibe banner. One is chosen at generation time
# and persisted in dna_data so the vibe keeps its colour on every load.
# No pink — the share button next to the vibe is already pink.
VIBE_COLORS = ["#40e7aa", "#ffa75e", "#8bbfff"]


def create_vibe_prompt(dna: dict) -> str:
    """
    Creates a detailed, few-shot prompt for the Gemini API to generate reading vibes.
    """
    # Extract the most salient data points from the DNA to feed the LLM
    reader_type = dna.get("reader_type", "Eclectic Reader")
    top_genres = [item[0] for item in dna.get("top_genres", [])[:3]]
    top_authors = [item[0] for item in dna.get("top_authors", [])[:3]]
    avg_pub_year = dna.get("user_stats", {}).get("avg_publish_year", 2000)

    # Simple logic to determine the era
    era = "classic" if avg_pub_year < 1980 else "modern"

    # StoryGraph mood data (empty for Goodreads uploads)
    mood_line = ""
    mood_distribution = dna.get("mood_distribution", [])
    if mood_distribution:
        top_moods = [f"{m[0]} ({m[1]})" for m in mood_distribution[:5]]
        mood_line = f"\n- Self-Reported Moods: {', '.join(top_moods)}"

    prompt = f"""
You are a witty, self-aware observer who writes funny, specific one-liner descriptions of a person based on their reading habits. Think: a friend affectionately roasting your taste in books — wry, literary, a little self-deprecating, with unexpected juxtapositions.

Your task is to generate 1 vivid, character-sketch-style sentence that captures this person's reading personality.

**RULES:**
- The sentence should be 8-18 words. Not a short phrase — a full, punchy sentence or description.
- All lowercase.
- No punctuation at the end.
- Be specific and visual — paint a scene or describe a character, don't just list genres.
- Be funny but not cringey. Wry and self-aware, not try-hard quirky.
- Do NOT mention specific book titles, author names, or genre names directly.
- Output ONLY a valid JSON object with a single key "vibe_phrases" which is a list of exactly 1 string.

**User's Reading DNA:**
- Primary Reader Type: "{reader_type}"
- Top Genres: {', '.join(top_genres)}
- Favorite Authors: {', '.join(top_authors)}
- General Era: {era}{mood_line}

**Example of GOOD output for a Fantasy/Sci-Fi reader:**
{{
  "vibe_phrases": [
    "the one who brings a 600-page paperback to the beach"
  ]
}}

**Example of GOOD output for a Literary Fiction reader:**
{{
  "vibe_phrases": [
    "staring out of train windows like a protagonist between chapters"
  ]
}}

**Example of GOOD output for a Nonfiction/History reader:**
{{
  "vibe_phrases": [
    "explaining the roman empire at dinner and losing the table"
  ]
}}

**Example of BAD output (Do NOT do this):**
{{
  "vibe_phrases": [
    "you enjoy reading fantasy books"
  ]
}}

Now, generate the JSON for the provided User's Reading DNA.
"""
    return prompt


def generate_vibe_with_llm(dna: dict) -> list:
    """
    Uses the Gemini API to generate a creative "vibe" for the user's DNA.
    """
    model = _gemini.client()
    if model is None:
        logger.warning("Vibe generation skipped because API key is not configured")
        return None

    prompt = create_vibe_prompt(dna)
    start = time.monotonic()

    def _latency_ms():
        return int((time.monotonic() - start) * 1000)

    try:
        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        response = model.generate_content(prompt, generation_config=generation_config)

        response_json = json.loads(response.text)
        vibe_phrases = response_json.get("vibe_phrases", [])

        if isinstance(vibe_phrases, list) and all(isinstance(p, str) for p in vibe_phrases):
            track_vibe_generation_completed(
                model=settings.GEMINI_MODEL,
                latency_ms=_latency_ms(),
                prompt_chars=len(prompt),
                response_chars=len(response.text),
            )
            # The dashboard shows a single sentence; keep only the first even if
            # the model over-delivers.
            return vibe_phrases[:1]
        else:
            logger.error(f"Vibe response had unexpected format: {response_json}")
            track_vibe_generation_failed(
                model=settings.GEMINI_MODEL,
                error_type="UnexpectedFormat",
                error_message=f"vibe_phrases missing or not a list of strings ({len(response.text)} chars)",
                latency_ms=_latency_ms(),
            )
            return None

    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON from response: {response.text}", exc_info=True)
        # Only the length goes to analytics - the raw text is vibe content.
        track_vibe_generation_failed(
            model=settings.GEMINI_MODEL,
            error_type="JSONDecodeError",
            error_message=f"invalid JSON in response ({len(response.text)} chars)",
            latency_ms=_latency_ms(),
        )
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        track_vibe_generation_failed(
            model=settings.GEMINI_MODEL,
            error_type=type(e).__name__,
            error_message=str(e),
            latency_ms=_latency_ms(),
        )
        return None
