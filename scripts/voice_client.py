"""End-to-end voice check without a browser: stream a WAV file into /ws/voice like a microphone.

Usage:
  python scripts/voice_client.py question.wav [--url ws://localhost:8002/ws/voice] [--out reply.wav]

The file is converted to 16 kHz mono PCM16 and sent in 80 ms frames at real-time speed,
followed by silence so the server's end-of-speech detection closes the turn. Prints every
event with timestamps and saves the spoken reply.
"""

import argparse
import asyncio
import io
import json
import time
import wave

import numpy as np
import websockets

RATE = 16_000
FRAME = 1280  # 80 ms


def load_pcm16(path: str) -> bytes:
    with wave.open(path) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        audio = np.interp(np.arange(0, len(audio), rate / RATE), np.arange(len(audio)), audio)
    return audio.astype("<i2").tobytes()


def merge_wavs(chunks: list[bytes]) -> bytes:
    frames, params = [], None
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk)) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    return out.getvalue()


async def run(args) -> int:
    pcm = load_pcm16(args.wav) + bytes(2 * RATE * 2)  # + 2 s of silence
    audio_out: list[bytes] = []
    t0 = time.perf_counter()

    def log(text: str) -> None:
        print(f"[{time.perf_counter() - t0:6.2f}s] {text}")

    async with websockets.connect(args.url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "mode": "vad", "tts": True}))

        async def sender():
            for i in range(0, len(pcm), FRAME * 2):
                await ws.send(pcm[i : i + FRAME * 2])
                await asyncio.sleep(FRAME / RATE)

        send_task = asyncio.create_task(sender())
        reply = []
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    audio_out.append(message)
                    log(f"audio chunk #{len(audio_out)} ({len(message)} bytes)")
                    continue
                msg = json.loads(message)
                kind = msg["type"]
                if kind == "token":
                    reply.append(msg["text"])
                    continue
                log(json.dumps(msg) if kind != "assistant_done" else f"assistant_done: {''.join(reply)!r}")
                if kind == "error":
                    return 1
                if kind == "turn_done":
                    break
        finally:
            send_task.cancel()

    if audio_out:
        with open(args.out, "wb") as f:
            f.write(merge_wavs(audio_out))
        log(f"saved spoken reply to {args.out}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--url", default="ws://localhost:8002/ws/voice")
    parser.add_argument("--out", default="reply.wav")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
