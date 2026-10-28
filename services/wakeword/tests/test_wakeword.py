import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import CHUNK_SAMPLES, Settings
from app.detector import StreamingDetector
from app.main import create_app


def loud(n_chunks: int) -> bytes:
    return (np.full(CHUNK_SAMPLES * n_chunks, 20000, dtype="<i2")).tobytes()


def quiet(n_chunks: int) -> bytes:
    return np.zeros(CHUNK_SAMPLES * n_chunks, dtype="<i2").tobytes()


def amplitude_detector(model_name="test", threshold=0.5, cooldown_s=1.0):
    """Scores a chunk by its loudness: a stand-in for a neural wake-word model."""
    resets = []
    det = StreamingDetector(
        model_name,
        score_fn=lambda chunk: float(np.abs(chunk).mean()) / 20000,
        reset_fn=lambda: resets.append(1),
        threshold=threshold,
        cooldown_s=cooldown_s,
    )
    return det, resets


def test_detector_frames_arbitrary_chunk_sizes():
    det, _ = amplitude_detector()
    audio = quiet(3) + loud(1)
    detections = []
    for i in range(0, len(audio), 1000):  # not a multiple of the frame size
        detections += det.process(audio[i : i + 1000])[0]
    assert len(detections) == 1
    assert detections[0].at_seconds == pytest.approx(4 * CHUNK_SAMPLES / 16000)


def test_detector_cooldown_and_reset():
    det, resets = amplitude_detector(cooldown_s=1.0)
    detections, peak = det.process(loud(5))  # 0.4 s of sustained "wake word"
    assert len(detections) == 1 and peak == pytest.approx(1.0)
    assert len(resets) == 1
    detections, _ = det.process(quiet(10) + loud(1))  # > 1 s later: fires again
    assert len(detections) == 1


class FakeFactory:
    def __init__(self):
        self.created = []

    def available(self):
        return ["test", "other"]

    def prepare(self):
        pass

    def create(self, model_name, threshold):
        if model_name not in self.available():
            raise ValueError(f"Unknown wake-word model '{model_name}'")
        self.created.append((model_name, threshold))
        return amplitude_detector(model_name, threshold)[0]


@pytest.fixture
def client():
    settings = Settings(wakeword_default="test")
    with TestClient(create_app(settings, FakeFactory())) as c:
        yield c


def test_models_and_health(client):
    assert client.get("/health").json()["status"] == "ok"
    body = client.get("/api/models").json()
    assert body["models"] == ["test", "other"] and body["default"] == "test"


def test_socket_reports_wake(client):
    with client.websocket_connect("/ws/wakeword?threshold=0.6") as ws:
        assert json.loads(ws.receive_text()) == {"type": "ready", "model": "test", "threshold": 0.6}
        ws.send_bytes(quiet(5))
        ws.send_bytes(loud(2))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "wake" and msg["model"] == "test" and msg["score"] == 1.0


def test_socket_rejects_unknown_model(client):
    with client.websocket_connect("/ws/wakeword?model=nope") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error" and "nope" in msg["message"]


def test_socket_rejects_bad_threshold(client):
    with client.websocket_connect("/ws/wakeword?threshold=3") as ws:
        assert json.loads(ws.receive_text())["type"] == "error"
