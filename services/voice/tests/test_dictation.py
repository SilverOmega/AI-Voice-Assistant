import time

import numpy as np

from app.engines import Transcript
from app.vad import Endpointer

from .conftest import FakeSTT, FakeTTS, chunks, make_client, silence, tone

FAST = {"dictation_partial_ms": 0}  # partials as soon as there is new speech


class CountingSTT(FakeSTT):
    """Says how much audio it got, so partials and finals can be told apart."""

    def __init__(self, delay: float = 0.0):
        super().__init__()
        self.delay = delay
        self.lengths: list[float] = []

    def transcribe(self, audio, language=None, quick=False) -> Transcript:
        super().transcribe(audio, language, quick)
        if self.delay:
            time.sleep(self.delay)
        seconds = round(len(audio) / 16_000, 1)
        self.lengths.append(seconds)
        return Transcript(text=f"heard {seconds}s", language="en", duration=seconds, elapsed=0.0)


def stream(ws, audio: bytes) -> None:
    for frame in chunks(audio):
        ws.send_bytes(frame)


def until_done(ws) -> list[dict]:
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == "done":
            return messages


def run(backend, stt, audio: bytes, start: dict | None = None, **settings) -> list[dict]:
    with make_client(backend, stt, FakeTTS(), **{**FAST, **settings}) as client:
        with client.websocket_connect("/ws/dictate") as ws:
            ws.send_json({"type": "start", **(start or {})})
            assert ws.receive_json() == {"type": "ready", "sample_rate": 16_000}
            stream(ws, audio)
            ws.send_json({"type": "stop"})
            return until_done(ws)


def test_partials_while_speaking_then_a_final_at_the_pause(backend):
    stt = CountingSTT()
    messages = run(backend, stt, tone(1.6) + silence(0.8))
    kinds = [m["type"] for m in messages]
    assert kinds.count("final") == 1 and kinds[-1] == "done"
    assert "partial" in kinds[: kinds.index("final")]
    final = next(m for m in messages if m["type"] == "final")
    # The final covers the whole phrase; partials only what was heard so far.
    partial_seconds = [float(m["text"].split()[1][:-1]) for m in messages if m["type"] == "partial"]
    assert max(partial_seconds) < float(final["text"].split()[1][:-1])
    # Partials are one quick pass; the final gets Whisper's full treatment.
    assert stt.quick[-1] is False and all(stt.quick[:-1])


def test_two_phrases_two_finals_in_order(backend):
    stt = CountingSTT()
    messages = run(backend, stt, tone(0.8) + silence(0.7) + tone(1.5) + silence(0.7))
    finals = [m["text"] for m in messages if m["type"] == "final"]
    assert len(finals) == 2
    assert float(finals[0].split()[1][:-1]) < float(finals[1].split()[1][:-1])
    # No partial arrives for a phrase after its final.
    last_final = max(i for i, m in enumerate(messages) if m["type"] == "final")
    assert all(m["type"] != "partial" for m in messages[last_final:])


def test_stop_mid_phrase_finishes_it(backend):
    stt = CountingSTT()
    messages = run(backend, stt, tone(1.2))  # no pause before stop
    assert [m["type"] for m in messages][-2:] == ["final", "done"]


def test_silence_only_gives_nothing(backend):
    stt = CountingSTT()
    messages = run(backend, stt, silence(1.5))
    assert messages == [{"type": "done"}]
    assert stt.calls == 0


def test_language_is_passed_and_checked(backend):
    stt = CountingSTT()
    run(backend, stt, tone(0.8) + silence(0.7), start={"language": "vi"})
    assert set(stt.languages) == {"vi"}
    with make_client(backend, CountingSTT(), FakeTTS(), **FAST) as client:
        with client.websocket_connect("/ws/dictate") as ws:
            ws.send_json({"type": "start", "language": "vietnamese!"})
            assert ws.receive_json()["type"] == "error"


def test_english_only_model_warns(backend):
    stt = CountingSTT()
    stt.multilingual = False
    with make_client(backend, stt, FakeTTS(), **FAST) as client:
        with client.websocket_connect("/ws/dictate") as ws:
            ws.send_json({"type": "start", "language": "vi"})
            error = ws.receive_json()
            assert error["type"] == "error" and "only understands English" in error["message"]
            assert ws.receive_json()["type"] == "ready"


def test_a_busy_recognizer_skips_partials_instead_of_queueing_them(backend):
    stt = CountingSTT(delay=0.3)
    messages = run(backend, stt, tone(3.0) + silence(0.8))
    # 3 s of speech arrives at once: without skipping that would be 7 partials of 0.4 s each.
    partial_calls = len(stt.lengths) - 1
    assert partial_calls <= 2
    assert [m["type"] for m in messages][-2:] == ["final", "done"]


class WarmingSTT(CountingSTT):
    def __init__(self, warm_delay: float):
        super().__init__()
        self.warm_delay = warm_delay
        self.events: list[str] = []

    def warm_up(self) -> None:
        self.events.append("warm")
        time.sleep(self.warm_delay)

    def transcribe(self, audio, language=None, quick=False) -> Transcript:
        self.events.append("partial" if quick else "final")
        return super().transcribe(audio, language, quick)


def test_the_model_is_woken_up_before_the_first_words(backend):
    stt = WarmingSTT(warm_delay=0.3)
    messages = run(backend, stt, tone(1.6) + silence(0.8))
    # Warm-up runs first, and partials skip rather than pile up behind it.
    assert stt.events[0] == "warm" and stt.events.count("warm") == 1
    assert stt.events[-1] == "final"
    assert [m["type"] for m in messages][-2:] == ["final", "done"]


class FakeWhisperModel:
    def __init__(self):
        self.calls: list[dict] = []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        info = type("Info", (), {"language": "en", "duration": len(audio) / 16_000})()
        return iter(()), info


def test_warm_up_skips_a_model_that_just_ran():
    from app.config import Settings
    from app.engines import WhisperSTT

    stt = WhisperSTT(Settings())
    stt._model = FakeWhisperModel()
    stt.warm_up()
    assert len(stt._model.calls) == 1 and stt._model.calls[0]["vad_filter"] is False
    stt.warm_up()  # just warmed
    stt.transcribe(np.zeros(16_000, np.float32))
    stt.warm_up()  # just used
    assert len(stt._model.calls) == 2


def test_current_audio_is_the_phrase_so_far():
    ep = Endpointer(start_ms=40, silence_ms=200, preroll_ms=100)
    assert len(ep.current_audio()) == 0
    ep.feed(tone(0.5))
    heard = ep.current_audio()
    assert ep.in_speech and 0.5 <= len(heard) / 16_000 <= 0.6
    assert heard.dtype == np.float32
    ep.feed(silence(0.4))
    assert not ep.in_speech and len(ep.current_audio()) == 0
