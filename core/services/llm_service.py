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
You are a witty, poetic observer who can distill the "vibe" of a person's reading list into short, aesthetic phrases. You are not a robot; you are creative and a little quirky.

Your task is to generate 4 short, evocative, lowercase phrases that capture the feeling of this person's reading DNA.

**RULES:**
- Phrases must be short (2-6 words).
- All lowercase.
- No punctuation at the end of phrases.
- Do NOT describe the user's reading habits directly (e.g., "you read fantasy"). Instead, evoke the *feeling* of those habits.
- Output ONLY a valid JSON object with a single key "vibe_phrases" which is a list of 4 strings.

**User's Reading DNA:**
- Primary Reader Type: "{reader_type}"
- Top Genres: {', '.join(top_genres)}
- Favorite Authors: {', '.join(top_authors)}
- General Era: {era}

**Example of GOOD output for a Fantasy/Classic reader:**
{{
  "vibe_phrases": [
    "dusty maps and forgotten prophecies",
    "the scent of old paper",
    "a quiet corner in a grand library",
    "a story that echoes through ages"
  ]
}}

**Example of BAD output (Do NOT do this):**
{{
  "vibe_phrases": [
    "You enjoy reading fantasy books.",
    "Your favorite author is Brandon Sanderson.",
    "You read a lot of classics.",
    "Your vibe is nerdy."
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
        model = genai.GenerativeModel("gemini-2.5-flash")
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
