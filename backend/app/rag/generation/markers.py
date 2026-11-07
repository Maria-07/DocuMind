"""Inline citation markers for models without native citations (e.g. OpenAI).

The model is asked to cite numbered sources as ``[2]`` / ``[1][3]`` / ``[1, 3]``. This parser turns a
streamed text into the same provider-neutral events Claude's native citations produce
(``TextDelta`` / ``CitationDelta`` / ``BlockEnd``), so ``CitationTracker`` renumbers, validates
and persists them identically. Markers can be split across stream deltas, so a possible partial
marker at the end of the buffer is held back until it is complete.
"""

from __future__ import annotations

import re

from app.rag.generation.base import BlockEnd, CitationDelta, GenerationEvent, SourceDocument, TextDelta

# [1] · [1, 3] · [1;3] — numbers 1-99 (sources are 1-based; [0], years etc. stay text).
_NUMBER = r"[1-9]\d?"
_MARKER = re.compile(rf"\[\s*({_NUMBER}(?:\s*[,;]\s*{_NUMBER})*)\s*\]")
_PARTIAL = re.compile(r"\[[\d\s,;]{0,12}$")


class MarkerParser:
    def __init__(self, sources: list[SourceDocument]) -> None:
        self.sources = sources
        self._buffer = ""
        # Full text of each source: what a marker cites (validated by CitationTracker).
        self._texts = [" ".join(s.passages) for s in sources]

    def feed(self, text: str) -> list[GenerationEvent]:
        self._buffer += text
        events: list[GenerationEvent] = []
        while True:
            match = _MARKER.search(self._buffer)
            if match is None:
                break
            before = self._buffer[: match.start()]
            if before:
                events.append(TextDelta(before))
            for number in (int(n) for n in re.split(r"\s*[,;]\s*", match.group(1).strip())):
                index = number - 1
                # Out-of-range numbers yield an empty quote, which the tracker rejects (and counts).
                cited = self._texts[index] if 0 <= index < len(self._texts) else ""
                events.append(CitationDelta(source_index=index, cited_text=cited))
            events.append(BlockEnd())
            self._buffer = self._buffer[match.end() :]

        partial = _PARTIAL.search(self._buffer)
        if partial is not None:
            safe, self._buffer = self._buffer[: partial.start()], self._buffer[partial.start() :]
        else:
            safe, self._buffer = self._buffer, ""
        if safe:
            events.append(TextDelta(safe))
        return events

    def flush(self) -> list[GenerationEvent]:
        rest, self._buffer = self._buffer, ""
        return [TextDelta(rest)] if rest else []
