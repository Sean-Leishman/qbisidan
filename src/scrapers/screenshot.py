"""Screenshot reader: an image becomes text, and the shared place classifier does the rest.

Deliberately not a classifier of its own. A screenshot and an Instagram caption are the same
kind of evidence once both are text, so both go through PlaceClassifierAgent -- adding this
source changed nothing about classification. Pinned to Gemini like reels, because only it takes
images here; the image itself is never stored, only what was read from it.
"""
import logging

logger = logging.getLogger(__name__)

PROMPT = """Read this screenshot for someone saving places to visit.

1. Transcribe every piece of readable text exactly: names, addresses, opening hours, prices,
   menu items, captions, handles. Keep line breaks. Skip phone status-bar clutter (time, battery).
2. Then one final line starting "Shows:" saying plainly what the screenshot is of, e.g.
   "Shows: a restaurant's Google Maps page" or "Shows: a menu" or "Shows: a meme".

Do not add anything that is not visible in the image."""


class ScreenshotReader:
    def __init__(self, api_key: str, model: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def read(self, image: bytes, mime_type: str = "image/jpeg") -> str:
        from google.genai import types
        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image, mime_type=mime_type or "image/jpeg"), PROMPT],
            config=types.GenerateContentConfig(
                max_output_tokens=2048, temperature=0,
                # transcription is mechanical; thinking would only crowd out the answer
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (response.text or "").strip()
