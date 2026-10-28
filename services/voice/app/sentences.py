"""Split a streaming LLM reply into speakable sentences so TTS can start early."""

import re

_SENTENCE_END = re.compile(r"(?<=[.!?…。！？])[\"')\]]*\s+|\n{2,}|\n(?=\s*(?:[-*•]|\d+[.)])\s)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MD_SYMBOLS = re.compile(r"[*_`#>|~]+")
_LIST_MARKER = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)
_WS = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    """Strip markdown and URLs that sound awful when read aloud."""
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("link", text)
    text = _LIST_MARKER.sub("", text)
    text = _MD_SYMBOLS.sub("", text)
    return _WS.sub(" ", text).strip()


class SentenceChunker:
    """Accumulates streamed text and emits complete sentences.

    Very short sentences are merged with the next one (fewer, more natural TTS
    chunks); the first chunk is allowed to be short so speech starts quickly.
    Overly long runs without punctuation are split at a comma or space.
    """

    def __init__(self, min_chars: int = 40, first_min_chars: int = 8, max_chars: int = 300):
        self.min_chars = min_chars
        self.first_min_chars = first_min_chars
        self.max_chars = max_chars
        self._buffer = ""
        self._held = ""
        self._emitted = 0

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        out: list[str] = []
        while True:
            match = _SENTENCE_END.search(self._buffer)
            if match:
                piece, self._buffer = self._buffer[: match.start()], self._buffer[match.end() :]
            elif len(self._buffer) > self.max_chars:
                cut = self._buffer.rfind(", ", 0, self.max_chars)
                if cut < self.max_chars // 3:
                    cut = self._buffer.rfind(" ", 0, self.max_chars)
                if cut <= 0:
                    cut = self.max_chars
                piece, self._buffer = self._buffer[: cut + 1], self._buffer[cut + 1 :]
            else:
                break
            out.extend(self._accept(piece))
        return out

    def _accept(self, piece: str) -> list[str]:
        combined = f"{self._held} {piece}".strip() if self._held else piece.strip()
        limit = self.first_min_chars if self._emitted == 0 else self.min_chars
        if len(clean_for_speech(combined)) < limit:
            self._held = combined
            return []
        self._held = ""
        spoken = clean_for_speech(combined)
        if not spoken:
            return []
        self._emitted += 1
        return [spoken]

    def flush(self) -> list[str]:
        rest = f"{self._held} {self._buffer}".strip()
        self._held = self._buffer = ""
        spoken = clean_for_speech(rest)
        return [spoken] if spoken else []
