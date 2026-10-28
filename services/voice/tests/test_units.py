import numpy as np

from app.sentences import SentenceChunker, clean_for_speech
from app.vad import Endpointer

from .conftest import chunks, silence, tone


def run(endpointer: Endpointer, audio: bytes):
    events = []
    for piece in chunks(audio):
        events.extend(endpointer.feed(piece))
    return events


def test_endpointer_detects_one_utterance():
    ep = Endpointer()
    events = run(ep, silence(0.5) + tone(1.0) + silence(1.2))
    assert [e.kind for e in events] == ["speech_start", "speech_end"]
    seconds = len(events[1].audio) / 16000
    assert 1.0 <= seconds <= 2.2  # pre-roll + speech + trailing silence


def test_endpointer_ignores_clicks_and_discards_short_bursts():
    ep = Endpointer()
    assert run(ep, silence(0.3) + tone(0.06) + silence(1.0)) == []
    events = run(ep, tone(0.2) + silence(1.2))
    assert [e.kind for e in events] == ["speech_start", "discarded"]


def test_endpointer_detects_speech_at_stream_start():
    ep = Endpointer()
    events = run(ep, tone(1.0) + silence(1.2))
    assert [e.kind for e in events] == ["speech_start", "speech_end"]


def test_endpointer_tracks_quiet_noise_floor():
    ep = Endpointer()
    run(ep, silence(2.0, noise=0.005))  # below the minimum threshold: learned gradually
    assert ep.noise_floor > 0.003
    assert not ep.in_speech


def test_endpointer_learns_steady_loud_noise():
    ep = Endpointer(max_utterance_s=2.0)
    events = run(ep, silence(3.0, noise=0.03))  # a fan louder than the minimum threshold
    assert [e.kind for e in events] == ["speech_start", "discarded"]
    assert ep.threshold > 0.03
    assert run(ep, silence(1.0, noise=0.03)) == []  # the fan no longer triggers
    events = run(ep, tone(1.0) + silence(1.2, noise=0.03))  # speech over the fan still does
    assert [e.kind for e in events] == ["speech_start", "speech_end"]


def speechlike(seconds: float) -> bytes:
    """A tone with a 3 Hz syllable envelope: loud, but with brief dips like real speech."""
    t = np.arange(int(seconds * 16000)) / 16000
    envelope = np.abs(np.sin(2 * np.pi * 3 * t))
    return (0.3 * envelope * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()


def test_endpointer_caps_utterance_length():
    ep = Endpointer(max_utterance_s=1.0)
    events = run(ep, speechlike(2.5))
    assert "speech_end" in [e.kind for e in events]


def test_sentence_chunker_streams_sentences():
    chunker = SentenceChunker()
    out = []
    for token in "Sure! The capital of France is Paris. It is known for the Eiffel Tower and great food. Anything else".split(" "):
        out += chunker.feed(token + " ")
    out += chunker.flush()
    assert out[0] == "Sure! The capital of France is Paris."  # short first sentence merged
    assert out[-1] == "Anything else"
    assert len(out) == 3


def test_sentence_chunker_splits_long_runs():
    chunker = SentenceChunker(max_chars=50)
    out = chunker.feed("word " * 40)
    assert out and all(len(s) <= 50 for s in out)


def test_clean_for_speech():
    text = "## Title\n- **Bold** item with [a link](http://x.y) and `code` see https://example.com"
    assert clean_for_speech(text) == "Title Bold item with a link and code see link"
