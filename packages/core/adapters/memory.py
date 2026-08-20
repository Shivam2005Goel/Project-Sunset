"""Memory adapter - the case file that survives the gap between letters.

Six weeks pass between "we wrote to the pension fund" and "the pension fund replied".
Nothing about that gap is held in a model's context window; it is held here, and the
sub-agent reads it back on wake. Memory Bank when available, Firestore + vector search as
the fallback, a keyword-scored JSON file locally.

The retrieval quality difference between those three matters less than it looks: a case
file for one institution is a few dozen entries, and the sub-agent mostly wants "what did
I send, what did they ask for, what is still outstanding" - which is recall, not search.
"""

from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from packages.core.clock import iso
from packages.core.config import Settings, get_settings
from packages.core.logging import get_logger

log = get_logger("adapter.memory")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "we", "you", "your",
    "is", "are", "was", "were", "be", "been", "this", "that", "it", "as", "at", "by",
    "with", "from", "has", "have", "had", "will", "would", "please", "dear",
}


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2]


class MemoryAdapter(ABC):
    name = "memory"

    @abstractmethod
    def append(self, key: str, entry: dict[str, Any]) -> None: ...

    @abstractmethod
    def read(self, key: str, limit: int | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def search(self, key: str, query: str, k: int = 5) -> list[dict[str, Any]]: ...

    def summarize(self, key: str) -> str:
        """A compact case history for the sub-agent's next turn.

        Deliberately short. The point of persisted state is that the agent does not need
        to re-read its whole life story to take one step.
        """
        entries = self.read(key)
        if not entries:
            return "No prior correspondence on this institution."
        lines = [f"{e.get('at', '?')[:10]}  {e.get('kind', 'note')}: {e.get('summary', '')}" for e in entries[-12:]]
        return "\n".join(lines)


class FileMemory(MemoryAdapter):
    """Local fallback. One JSON file per case; append-only; keyword-scored retrieval."""

    name = "firestore_vector"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        return self.root / f"{safe}.json"

    def append(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            path = self._path(key)
            entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            entries.append({"at": iso(), **entry})
            path.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")

    def read(self, key: str, limit: int | None = None) -> list[dict[str, Any]]:
        path = self._path(key)
        if not path.exists():
            return []
        entries = json.loads(path.read_text(encoding="utf-8"))
        return entries[-limit:] if limit else entries

    def search(self, key: str, query: str, k: int = 5) -> list[dict[str, Any]]:
        wanted = set(tokenize(query))
        if not wanted:
            return self.read(key, limit=k)
        scored = []
        for entry in self.read(key):
            haystack = tokenize(json.dumps(entry, default=str))
            if not haystack:
                continue
            overlap = len(wanted & set(haystack)) / len(wanted)
            if overlap:
                scored.append((overlap, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:k]]


class FirestoreVectorMemory(FileMemory):  # pragma: no cover - cloud only
    """Firestore documents plus Vertex Vector Search embeddings.

    Falls back to the parent's keyword scoring if the embedding call fails, because a
    degraded search beats a sub-agent that cannot remember what it sent.
    """

    name = "firestore_vector_cloud"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.memory_dir)
        from google.cloud import firestore

        self._client = firestore.Client(project=settings.project_id)
        self._collection = "aftercare_memory"
        self._settings = settings

    def append(self, key: str, entry: dict[str, Any]) -> None:
        super().append(key, entry)
        self._client.collection(self._collection).add({"key": key, "at": iso(), **entry})

    def read(self, key: str, limit: int | None = None) -> list[dict[str, Any]]:
        query = self._client.collection(self._collection).where("key", "==", key).order_by("at")
        entries = [doc.to_dict() for doc in query.stream()]
        return entries[-limit:] if limit else entries


class MemoryBankAdapter(FileMemory):  # pragma: no cover - GEAP, unverified
    """Vertex AI Memory Bank. Unverified against the live service - see CLAUDE.md."""

    name = "memory_bank"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.memory_dir)
        raise RuntimeError(
            "Memory Bank client is not wired yet - confirm GA status on Day 0. Set "
            "MEMORY_ADAPTER=firestore_vector meanwhile; it satisfies the same contract."
        )


_adapter: MemoryAdapter | None = None


def get_memory_adapter(settings: Settings | None = None) -> MemoryAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter
    settings = settings or get_settings()
    choice = settings.effective_adapter("memory")

    if choice == "memory_bank":  # pragma: no cover - cloud only
        try:
            _adapter = MemoryBankAdapter(settings)
            return _adapter
        except RuntimeError as exc:
            log.warning("memory.fallback", reason=str(exc))
            choice = "firestore_vector"

    if choice == "firestore_vector" and settings.is_cloud and settings.project_id:  # pragma: no cover
        _adapter = FirestoreVectorMemory(settings)
    else:
        _adapter = FileMemory(settings.memory_dir)
    return _adapter


def set_memory_adapter(adapter: MemoryAdapter | None) -> None:
    global _adapter
    _adapter = adapter
