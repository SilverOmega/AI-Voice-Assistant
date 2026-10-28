import json

from .conftest import FakeChatBackend, FakeSTT, FakeTTS, chunks, make_client, silence, tone


def collect(ws, until: str = "turn_done", limit: int = 200):
    """Receive frames until a JSON message of type `until`; return (json_msgs, binary_frames)."""
    msgs, audio = [], []
    for _ in range(limit):
        frame = ws.receive()
        if frame.get("bytes") is not None:
            audio.append(frame["bytes"])
            continue
        msg = json.loads(frame["text"])
        msgs.append(msg)
        if msg["type"] == until:
            return msgs, audio
    raise AssertionError(f"never received {until}: {msgs}")


def types(msgs):
    return [m["type"] for m in msgs]


def test_health_and_rest_endpoints(client):
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["chat_service"] is True

    wav = client.post("/api/tts", json={"text": "Hello"})
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav"

    stt = client.post("/api/stt", files={"file": ("a.webm", b"fake-bytes", "audio/webm")})
    assert stt.json()["text"] == "what time is it"

    assert client.post("/api/stt", files={"file": ("a.webm", b"", "audio/webm")}).status_code == 400
    assert client.get("/api/voices").json()["voices"] == ["en_US-test-medium"]


def test_vad_voice_turn_end_to_end(client, backend, fakes):
    stt, tts = fakes
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "vad"}))
        msgs, _ = collect(ws, until="state")
        assert msgs[0]["type"] == "ready"

        for piece in chunks(silence(0.4) + tone(1.0) + silence(1.2)):
            ws.send_bytes(piece)
        msgs, audio = collect(ws)

    kinds = types(msgs)
    assert {"type": "vad", "speaking": True} in msgs
    transcript = next(m for m in msgs if m["type"] == "transcript")
    assert transcript["text"] == "what time is it"
    assert {"type": "session", "session_id": "sess-1"} in msgs
    assert kinds.index("transcript") < kinds.index("assistant_start") < kinds.index("tts_done")
    assert "".join(m["text"] for m in msgs if m["type"] == "token").strip() == backend.reply
    assert backend.received == [{"content": "what time is it", "source": "voice"}]
    # one WAV per sentence, in order
    assert tts.spoken == ["It is noon.", "Have a great lunch!"]
    assert audio == [b"RIFF" + s.encode() for s in tts.spoken]


def test_push_to_talk_and_existing_session(client, backend, fakes):
    stt, _ = fakes
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "ptt", "session_id": "sess-1"}))
        collect(ws, until="state")
        ws.send_bytes(silence(2.0))  # ignored: not recording
        ws.send_text(json.dumps({"type": "ptt_start"}))
        ws.send_bytes(tone(0.5))
        ws.send_text(json.dumps({"type": "ptt_end"}))
        msgs, _ = collect(ws)
    assert stt.calls == 1
    assert "session" not in types(msgs)  # session already existed


def test_typed_text_with_browser_tts():
    backend, stt, tts = FakeChatBackend(), FakeSTT(), FakeTTS()
    with make_client(backend, stt, tts, tts_engine="browser") as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            collect(ws, until="state")
            ws.send_text(json.dumps({"type": "text", "text": "hi"}))
            msgs, audio = collect(ws)
    assert audio == [] and tts.spoken == []
    assert [m["text"] for m in msgs if m["type"] == "speak"] == ["It is noon.", "Have a great lunch!"]


def test_empty_transcript_does_not_call_chat():
    backend, stt, tts = FakeChatBackend(), FakeSTT(text=""), FakeTTS()
    with make_client(backend, stt, tts) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "start", "mode": "ptt"}))
            collect(ws, until="state")
            ws.send_text(json.dumps({"type": "ptt_start"}))
            ws.send_bytes(tone(0.5))
            ws.send_text(json.dumps({"type": "ptt_end"}))
            msgs, _ = collect(ws, until="transcript")
            msgs += collect(ws, until="state")[0]
    assert msgs[-1] == {"type": "state", "state": "listening"}
    assert backend.received == []


def test_cancel_interrupts_turn():
    backend = FakeChatBackend(reply=" ".join(["word"] * 200), delay=0.02)
    with make_client(backend, FakeSTT(), FakeTTS()) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            collect(ws, until="state")
            ws.send_text(json.dumps({"type": "text", "text": "tell me a long story"}))
            collect(ws, until="token")
            ws.send_text(json.dumps({"type": "cancel"}))
            msgs, _ = collect(ws, until="interrupted")
            assert "turn_done" not in types(msgs)
            # the socket stays usable after an interruption
            backend.reply, backend.delay = "Okay.", 0
            ws.send_text(json.dumps({"type": "text", "text": "short one"}))
            msgs, _ = collect(ws)
            assert "assistant_done" in types(msgs)


def test_unknown_message_reports_error(client):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_text(json.dumps({"type": "bogus"}))
        assert json.loads(ws.receive_text())["type"] == "error"
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["message"] == "Invalid JSON"


def test_start_message_mid_turn_does_not_reset_state():
    backend = FakeChatBackend(reply="One two three. Four five six.", delay=0.02)
    with make_client(backend, FakeSTT(), FakeTTS()) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            collect(ws, until="state")
            ws.send_text(json.dumps({"type": "text", "text": "count"}))
            msgs, _ = collect(ws, until="token")
            # the UI re-sends options when it learns the new session id
            ws.send_text(json.dumps({"type": "start", "session_id": "sess-1"}))
            msgs, _ = collect(ws)
    assert {"type": "state", "state": "listening"} not in msgs
    assert "ready" not in types(msgs)
