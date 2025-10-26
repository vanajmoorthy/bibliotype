import logging
import os
import re
import json
import requests
import google.generativeai as genai
from urllib.parse import quote

logger = logging.getLogger(__name__)

# This should already be configured in your project from the vibe generation
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

BIG_5_PUBLISHERS = [
    "Penguin Random House",
    "Hachette Livre",
    "HarperCollins",
    "Macmillan Publishers",
    "Simon & Schuster",
]


def research_publisher_identity(publisher_name: str, session: requests.Session) -> dict:
    """
    Uses Wikipedia and an LLM to determine a publisher's parent company and mainstream status.
    Now tries multiple search queries to find the most relevant Wikipedia page.
    """
    findings = {"is_mainstream": False, "parent_company_name": None, "reasoning": None, "error": None}

    if not GEMINI_API_KEY:
        findings["error"] = "GEMINI_API_KEY not configured."
        return findings

    try:
        # --- NEW: Create a list of potential search terms ---
        search_terms = [
            f"{publisher_name} (publisher)",
            f"{publisher_name} (imprint)",
            publisher_name,
        ]

        context_text = ""
        found_page_title = None

        # --- NEW: Loop through search terms to find the best page ---
        for term in search_terms:
            encoded_name = quote(term)
            wiki_api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_name}"
            res_wiki = session.get(wiki_api_url, timeout=10)

            if res_wiki.status_code == 200:
                wiki_json = res_wiki.json()
                # Check if the summary is not about a disambiguation page
                if "extract" in wiki_json and "may refer to" not in wiki_json["extract"].lower():
                    context_text = wiki_json["extract"]
                    found_page_title = wiki_json.get("title", term)
                    logger.info(f"Found Wikipedia page '{found_page_title}' for '{publisher_name}'")
                    break  # We found a good page, stop searching

        if not context_text:
            findings["error"] = f"Could not find a relevant Wikipedia page for '{publisher_name}'."
            return findings

        # --- Step 2: Construct a precise prompt for Gemini ---
        prompt = f"""
        You are a publishing industry analyst. Your task is to analyze the provided text about a publisher and determine if it is a major publisher or an imprint of one of the "Big 5". The "Big 5" are: {', '.join(BIG_5_PUBLISHERS)}.

        I searched for the publisher "{publisher_name}", and the most relevant Wikipedia page found was for "{found_page_title}". The summary of this page is provided below. This text might describe the publisher directly, or it might describe its parent company.

        Here is the summary text:
        ---
        {context_text}
        ---

        Based ONLY on the text provided, answer the following. Your response MUST be in JSON format.
        1. "is_mainstream": Is the original publisher "{publisher_name}" (or its parent company described in the text) one of the Big 5 OR an imprint/division of one of them? (true/false)
        2. "parent_company_name": If a Big 5 parent company is mentioned in the text, what is its name? If it's independent or a parent itself, this should be null.
        3. "reasoning": Briefly explain your decision in one sentence, referencing the original publisher name.

        Example JSON response for "Viking Press":
        {{
            "is_mainstream": true,
            "parent_company_name": "Penguin Random House",
            "reasoning": "The text for Penguin Group (the parent) shows it is part of Penguin Random House, making the imprint Viking Press mainstream."
        }}

        JSON Response:
        """

        # --- Step 3: Call the Gemini API ---
        model = genai.GenerativeModel("models/gemini-2.5-flash")  # Or your preferred model
        response = model.generate_content(prompt)

        # Clean up the response to extract only the JSON part
        json_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        ai_findings = json.loads(json_text)

        if "is_mainstream" in ai_findings:
            findings["is_mainstream"] = ai_findings["is_mainstream"]
        if "parent_company_name" in ai_findings:
            findings["parent_company_name"] = ai_findings["parent_company_name"]
        if "reasoning" in ai_findings:
            findings["reasoning"] = ai_findings["reasoning"]

    except Exception as e:
        findings["error"] = f"An unexpected error occurred: {e}"

    return findings
