"""Stands in for the `claude` program in tests: prints canned stream-json for a few prompts."""

import json
import os
import sys
import time

args = sys.argv[1:]


def out(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def delta(text: str) -> None:
    out({
        "type": "stream_event", "parent_tool_use_id": None,
        "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
    })


def result(text: str = "", is_error: bool = False, **extra) -> None:
    out({"type": "result", "subtype": "success", "is_error": is_error, "result": text, **extra})


def synthetic_error(code: str, text: str) -> None:
    out({"type": "assistant", "error": code, "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}})


if args == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    signed_in = not os.environ.get("FAKE_SIGNED_OUT")
    print(json.dumps({"loggedIn": signed_in, "authMethod": "claude.ai", "subscriptionType": "pro", "email": "someone@example.com"}))
    sys.exit(0)

message = json.loads(sys.stdin.readline())["message"]
text = next(b["text"] for b in message["content"] if b["type"] == "text")
images = [b for b in message["content"] if b["type"] == "image"]
out({"type": "system", "subtype": "init", "tools": []})

if "--json-schema" in args:
    schema = json.loads(args[args.index("--json-schema") + 1])
    result(json.dumps({"ok": True}), structured_output={k: True for k in schema.get("properties", {})}, stop_reason="tool_use")
elif "limit" in text:
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": time.time() + 3600}})
    synthetic_error("rate_limit", "You've hit your limit")
    result("You've hit your limit", is_error=True, api_error_status=429)
elif "signed out" in text:
    synthetic_error("authentication_failed", "Invalid API key · Please run /login")
    result("Invalid API key · Please run /login", is_error=True, api_error_status=401)
elif "slow" in text:
    time.sleep(30)
elif "crash" in text:
    sys.stderr.write("boom: something broke\n")
    sys.exit(3)
elif "long" in text:
    delta("cut")
    out({"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}})
    result("cut", stop_reason="max_tokens")
elif "env" in text:
    report = {
        "max_tokens": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"),
        "api_key": "ANTHROPIC_API_KEY" in os.environ,
        "folder": os.listdir("."),
        "system": args[args.index("--system-prompt") + 1],
        "tools": args[args.index("--tools") + 1],
    }
    delta(json.dumps(report))
    result(json.dumps(report))
elif "echo" in text:
    delta(json.dumps({"text": text, "images": len(images), "system": args[args.index("--system-prompt") + 1]}))
    result("")
else:
    delta("Hello")
    delta(" world")
    out({"type": "stream_event", "parent_tool_use_id": "toolu_x", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "IGNORED"}}})
    result("Hello world", stop_reason="end_turn")
