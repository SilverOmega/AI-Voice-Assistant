"""Round-trip check of the real speech engines: Piper speaks a phrase, Whisper transcribes it.

Usage (from services/voice):  python -m scripts.smoke_test ["phrase to speak"]
Downloads the configured Piper voice and Whisper model on first run.
"""

import io
import sys
import time
import wave

from app.config import get_settings
from app.engines import PiperTTS, WhisperSTT


def main() -> int:
    phrase = sys.argv[1] if len(sys.argv) > 1 else "Hello! Please set a timer for ten minutes."
    settings = get_settings()

    started = time.perf_counter()
    tts = PiperTTS(settings)
    tts.load()
    print(f"piper voice '{settings.piper_voice}' loaded in {time.perf_counter() - started:.1f}s")

    started = time.perf_counter()
    wav = tts.synthesize(phrase)
    with wave.open(io.BytesIO(wav)) as w:
        audio_seconds = w.getnframes() / w.getframerate()
    print(f"synthesized {audio_seconds:.1f}s of audio in {time.perf_counter() - started:.2f}s")

    started = time.perf_counter()
    stt = WhisperSTT(settings)
    stt.load()
    print(f"whisper '{settings.stt_model}' loaded in {time.perf_counter() - started:.1f}s")

    result = stt.transcribe(io.BytesIO(wav))
    print(f"transcribed in {result.elapsed:.2f}s: {result.text!r}")

    expected = {w.strip(".,!?").lower() for w in phrase.split()}
    heard = {w.strip(".,!?").lower() for w in result.text.split()}
    overlap = len(expected & heard) / max(1, len(expected))
    print(f"word overlap: {overlap:.0%}")
    return 0 if overlap >= 0.6 else 1


if __name__ == "__main__":
    sys.exit(main())
