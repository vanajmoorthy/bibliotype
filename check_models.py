# In check_models.py

import os
import google.generativeai as genai
from dotenv import load_dotenv

# Load the API key from your .env file
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ ERROR: GEMINI_API_KEY not found in your .env file.")
else:
    try:
        genai.configure(api_key=api_key)
        print("✅ Successfully configured with API key.")
        print("-" * 30)
        print("Searching for available models that support 'generateContent'...\n")

        found_models = []
        for m in genai.list_models():
            # This is the crucial check: we only want models that can generate text
            if "generateContent" in m.supported_generation_methods:
                found_models.append(m.name)

        if found_models:
            print("🎉 Found the following usable models:")
            for model_name in found_models:
                print(f"  - {model_name}")
            print("\nRECOMMENDATION: Use the first model in this list in your llm_service.py file.")
        else:
            print("⚠️ No usable models found. Check your API key permissions in the Google AI Studio.")

    except Exception as e:
        print(f"❌ An error occurred: {e}")
