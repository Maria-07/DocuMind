"""Structure-aware, token-aware chunking.

Rules (see docs/architecture.md §2.2):
  1. Never cross a hard boundary: page (PDF), section (DOCX), sheet (Excel).
  2. Prose is split with LangChain's RecursiveCharacterTextSplitter measured in *model tokens*.
  3. Tables stay whole when they fit; otherwise they are split by rows with the header repeated.
  4. Spreadsheet rows are packed into chunks with the column header line repeated in each chunk.
  5. Exact duplicate chunks within a document (repeated boilerplate) are dropped.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.chunking.tokens import TokenCounter
from app.rag.ingestion.types import Block, ParsedDocument

# A text block this small that introduces a sub-section is merged into the sub-section.
_TINY_BLOCK_TOKENS = 32
_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    kind: str
    text: str
    token_count: int
    content_hash: str
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_path: str | None = None


@dataclass(frozen=True, slots=True)
class _Piece:
    kind: str
    text: str
    block: Block
    row_start: int | None = None
    row_end: int | None = None


class Chunker:
    def __init__(self, counter: TokenCounter, *, chunk_size: int, chunk_overlap: int) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.counter = counter
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=counter.count,
            separators=_SEPARATORS,
            keep_separator="end",
            strip_whitespace=True,
        )

    def _split_record(self, line: str, budget: int) -> list[str]:
        """Split one oversized row/table line to fit ``budget`` (what is left after the repeated header).

        Records are discrete, so no overlap.
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=budget, chunk_overlap=0, length_function=self.counter.count, separators=_SEPARATORS
        )
        return splitter.split_text(line)

    def chunk(self, parsed: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        seen: set[str] = set()
        for piece in self._pieces(self._merge_tiny_intro_blocks(parsed.blocks)):
            text = piece.text.strip()
            if not text:
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            b = piece.block
            chunks.append(
                Chunk(
                    index=len(chunks),
                    kind=piece.kind,
                    text=text,
                    token_count=self.counter.count(text),
                    content_hash=digest,
                    page_start=b.page,
                    page_end=b.page,
                    sheet_name=b.sheet,
                    row_start=piece.row_start,
                    row_end=piece.row_end,
                    section_path=b.section_path,
                )
            )
        return chunks

    # --- block preparation -----------------------------------------------------------------
    def _merge_tiny_intro_blocks(self, blocks: list[Block]) -> list[Block]:
        """Fold e.g. a bare "Security" heading into the "Security > Passwords" block that follows."""
        merged: list[Block] = []
        pending: Block | None = None
        for block in blocks:
            if pending is not None:
                parent = pending.section_path or ""
                if (
                    block.kind == "text"
                    and block.page == pending.page
                    and block.sheet == pending.sheet
                    and (block.section_path or "").startswith(parent)
                ):
                    block = replace(block, text=f"{pending.text}\n\n{block.text}")
                else:
                    merged.append(pending)
                pending = None
            if block.kind == "text" and self.counter.count(block.text) < _TINY_BLOCK_TOKENS:
                pending = block
                continue
            merged.append(block)
        if pending is not None:
            merged.append(pending)
        return merged

    def _pieces(self, blocks: list[Block]) -> Iterator[_Piece]:
        for block in blocks:
            if block.kind == "text":
                for text in self._splitter.split_text(block.text):
                    yield _Piece("text", text, block)
            elif block.kind == "table":
                yield from self._table_pieces(block)
            elif block.kind == "rows":
                yield from self._row_pieces(block)

    def _table_pieces(self, block: Block) -> Iterator[_Piece]:
        if self.counter.count(block.text) <= self.chunk_size:
            yield _Piece("table", block.text, block)
            return
        lines = block.text.split("\n")
        header = "\n".join(lines[:2])  # Markdown header + separator
        yield from (
            _Piece("table", text, block) for text, _, _ in self._pack(header, [(None, line) for line in lines[2:]])
        )

    def _row_pieces(self, block: Block) -> Iterator[_Piece]:
        header = f"Columns: {block.header}" if block.header else ""
        records = [(number, f"Row {number}: {line}") for number, line in block.rows]
        for text, start, end in self._pack(header, records):
            yield _Piece("rows", text, block, row_start=start, row_end=end)

    def _pack(self, header: str, records: list[tuple[int | None, str]]) -> Iterator[tuple[str, int | None, int | None]]:
        """Greedily pack records under a repeated header without exceeding chunk_size."""
        header_tokens = self.counter.count(header) + 1 if header else 0
        budget = max(self.chunk_size - header_tokens, self.chunk_size // 4)
        current: list[str] = []
        current_tokens = 0
        start: int | None = None
        end: int | None = None

        def emit() -> tuple[str, int | None, int | None]:
            body = "\n".join(current)
            return (f"{header}\n{body}" if header else body), start, end

        for number, line in records:
            line_tokens = self.counter.count(line) + 1
            if line_tokens > budget:
                # A single record larger than a chunk: flush, then split the record on its own.
                if current:
                    yield emit()
                    current, current_tokens, start = [], 0, None
                for part in self._split_record(line, budget):
                    yield (f"{header}\n{part}" if header else part), number, number
                continue
            if current and current_tokens + line_tokens > budget:
                yield emit()
                current, current_tokens, start = [], 0, None
            if start is None:
                start = number
            end = number
            current.append(line)
            current_tokens += line_tokens
        if current:
            yield emit()
