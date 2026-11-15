"""Voice-note transcriber: audio bytes in, verbatim transcript out.

Pinned to Gemini like screenshots and reels, because only it takes audio here. The audio goes
inline rather than through the Files API: a voice note is tens of KB, and an inline request
tops out around 20 MB, about where Telegram's own download cap sits anyway.
"""
import logging

logger = logging.getLogger(__name__)

PROMPT = """Transcribe this voice note verbatim, in the language it is spoken in.

Output only the transcript: no preamble, no labels, no timestamps, no notes about the audio.
If there is no speech, output nothing."""


class VoiceTranscriber:
    def __init__(self, api_key: str, model: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def transcribe(self, audio: bytes, mime_type: str = "audio/ogg") -> str:
        from google.genai import types
        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=audio, mime_type=mime_type or "audio/ogg"), PROMPT],
            config=types.GenerateContentConfig(
                max_output_tokens=8192, temperature=0,
                # transcription is mechanical; thinking would only crowd out the answer
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return (response.text or "").strip()
