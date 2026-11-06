"""Token counting with the embedding model's own tokenizer, so chunk sizes are real model tokens."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class HuggingFaceTokenCounter:
    def __init__(self, tokenizer_name: str, cache_dir: Path | None = None) -> None:
        # Only tokenizer.json (~700 KB) is fetched, into the same cache as the embedding models.
        path = hf_hub_download(
            repo_id=tokenizer_name,
            filename="tokenizer.json",
            cache_dir=str(cache_dir.resolve()) if cache_dir is not None else None,
        )
        self._tokenizer = Tokenizer.from_file(path)
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)
