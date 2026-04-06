import json
import logging
import os

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)
else:
    logger.warning("GEMINI_API_KEY environment variable not found. Vibe generation will be disabled.")


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

    prompt = f"""
You are a witty, self-aware observer who writes funny, specific one-liner descriptions of a person based on their reading habits. Think: a friend affectionately roasting your taste in books — wry, literary, a little self-deprecating, with unexpected juxtapositions.

Your task is to generate 2 vivid, character-sketch-style sentences that capture this person's reading personality.

**RULES:**
- Each sentence should be 8-18 words. Not short phrases — full, punchy sentences or descriptions.
- All lowercase.
- No punctuation at the end.
- Be specific and visual — paint a scene or describe a character, don't just list genres.
- Be funny but not cringey. Wry and self-aware, not try-hard quirky.
- Do NOT mention specific book titles, author names, or genre names directly.
- Output ONLY a valid JSON object with a single key "vibe_phrases" which is a list of 2 strings.

**User's Reading DNA:**
- Primary Reader Type: "{reader_type}"
- Top Genres: {', '.join(top_genres)}
- Favorite Authors: {', '.join(top_authors)}
- General Era: {era}

**Example of GOOD output for a Fantasy/Sci-Fi reader:**
{{
  "vibe_phrases": [
    "accidentally falling asleep in someone else's imaginary world again",
    "the one who brings a 600-page paperback to the beach"
  ]
}}

**Example of GOOD output for a Literary Fiction reader:**
{{
  "vibe_phrases": [
    "staring out of train windows like a protagonist between chapters",
    "collecting existential crises from other people's novels"
  ]
}}

**Example of GOOD output for a Nonfiction/History reader:**
{{
  "vibe_phrases": [
    "explaining the roman empire at dinner and losing the table",
    "quietly judging everyone who hasn't read the footnotes"
  ]
}}

**Example of BAD output (Do NOT do this):**
{{
  "vibe_phrases": [
    "dusty maps and forgotten prophecies",
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
    if not api_key:
        logger.warning("Vibe generation skipped because API key is not configured")
        return ["vibe generation disabled", "please configure api key"]

    prompt = create_vibe_prompt(dna)

    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        response = model.generate_content(prompt, generation_config=generation_config)

        response_json = json.loads(response.text)
        vibe_phrases = response_json.get("vibe_phrases", [])

        if isinstance(vibe_phrases, list) and all(isinstance(p, str) for p in vibe_phrases):
            return vibe_phrases
        else:
            return ["error parsing vibe", "unexpected format received"]

    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from response: {response.text}", exc_info=True)
        return ["error generating vibe", "invalid json response"]
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return ["error generating vibe", "api call failed"]
