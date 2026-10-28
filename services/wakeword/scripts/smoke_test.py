"""Run the real openWakeWord detector over a WAV file and print detections.

Usage (from services/wakeword):  python -m scripts.smoke_test recording.wav [model] [threshold]
Any sample rate / channel count is accepted; audio is converted to 16 kHz mono.
Exit code 0 if the wake word was detected.
"""

import sys
import wave

import numpy as np

from app.config import SAMPLE_RATE, get_settings
from app.detector import OpenWakeWordFactory


def load_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        positions = np.arange(0, len(audio), rate / SAMPLE_RATE)
        audio = np.interp(positions, np.arange(len(audio)), audio)
    return audio.astype("<i2")


def main() -> int:
    path = sys.argv[1]
    settings = get_settings()
    model = sys.argv[2] if len(sys.argv) > 2 else settings.wakeword_default
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else settings.wakeword_threshold

    audio = load_wav(path)
    padding = np.zeros(SAMPLE_RATE, dtype="<i2")  # 1 s of silence either side
    stream = np.concatenate([padding, audio, padding]).tobytes()

    detector = OpenWakeWordFactory(settings).create(model, threshold)
    found, peak = [], 0.0
    for i in range(0, len(stream), 1600):  # 50 ms network-sized chunks
        detections, chunk_peak = detector.process(stream[i : i + 1600])
        found += detections
        peak = max(peak, chunk_peak)
    print(f"model={model} threshold={threshold} peak_score={peak:.3f}")
    for d in found:
        print(f"  detected at {d.at_seconds:.2f}s score={d.score:.3f}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
