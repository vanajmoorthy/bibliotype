# In check_models.py

import logging
import os
import google.generativeai as genai
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Load the API key from your .env file
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    logger.error("GEMINI_API_KEY not found in your .env file.")
else:
    try:
        genai.configure(api_key=api_key)
        logger.info("Successfully configured with API key.")
        logger.info("-" * 30)
        logger.info("Searching for available models that support 'generateContent'...\n")

        found_models = []
        for m in genai.list_models():
            # This is the crucial check: we only want models that can generate text
            if "generateContent" in m.supported_generation_methods:
                found_models.append(m.name)

        if found_models:
            logger.info("🎉 Found the following usable models:")
            for model_name in found_models:
                logger.info(f"  - {model_name}")
            logger.info("\nRECOMMENDATION: Use the first model in this list in your llm_service.py file.")
        else:
            logger.warning("No usable models found. Check your API key permissions in the Google AI Studio.")

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
