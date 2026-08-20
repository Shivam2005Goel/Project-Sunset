"""Document store.

Firestore in cloud mode, a JSON directory in local mode. The interface is deliberately
Firestore-shaped (collection / document id / dict) so that the local implementation is a
faithful stand-in rather than a convenient lie: if your query needs a join, it will fail
locally for the same reason it would fail in Firestore.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

from packages.core.config import Settings, get_settings


class DocumentStore(ABC):
    @abstractmethod
    def put(self, collection: str, doc_id: str, data: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def delete(self, collection: str, doc_id: str) -> None: ...

    @abstractmethod
    def all(self, collection: str) -> list[dict[str, Any]]: ...

    def query(self, collection: str, **equals: Any) -> list[dict[str, Any]]:
        """Equality filters only - the intersection of what Firestore does cheaply."""
        rows = self.all(collection)
        if not equals:
            return rows
        return [
            row
            for row in rows
            if all(_matches(row.get(key), value) for key, value in equals.items())
        ]

    def first(self, collection: str, **equals: Any) -> dict[str, Any] | None:
        rows = self.query(collection, **equals)
        return rows[0] if rows else None

    def count(self, collection: str, **equals: Any) -> int:
        return len(self.query(collection, **equals))

    def clear(self, collections: Iterable[str] | None = None) -> None:  # pragma: no cover
        raise NotImplementedError


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return actual in expected
    return actual == expected


class JsonDocumentStore(DocumentStore):
    """One JSON file per collection. Atomic writes, process-local lock.

    Good enough for a seeded estate of a few hundred documents, which is the only thing
    local mode is ever asked to hold.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}

    def _path(self, collection: str) -> Path:
        return self.root / f"{collection}.json"

    def _load(self, collection: str) -> dict[str, dict[str, Any]]:
        if collection in self._cache:
            return self._cache[collection]
        path = self._path(collection)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
        self._cache[collection] = data
        return data

    def _flush(self, collection: str) -> None:
        path = self._path(collection)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._cache[collection], indent=2, default=str, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def put(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            docs = self._load(collection)
            docs[doc_id] = data
            self._flush(collection)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load(collection).get(doc_id)

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            docs = self._load(collection)
            if doc_id in docs:
                del docs[doc_id]
                self._flush(collection)

    def all(self, collection: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._load(collection).values())

    def clear(self, collections: Iterable[str] | None = None) -> None:
        with self._lock:
            targets = list(collections) if collections else [
                p.stem for p in self.root.glob("*.json")
            ]
            for collection in targets:
                self._cache[collection] = {}
                self._flush(collection)


class FirestoreDocumentStore(DocumentStore):  # pragma: no cover - cloud only
    """Firestore Native mode.

    Untested against a live project as of this commit - see the honesty note at the
    bottom of CLAUDE.md. Day 10 is where this gets exercised.
    """

    def __init__(self, project_id: str, namespace: str = "aftercare") -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project_id)
        self._namespace = namespace

    def _col(self, collection: str):
        return self._client.collection(f"{self._namespace}_{collection}")

    def put(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self._col(collection).document(doc_id).set(data)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        snap = self._col(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def delete(self, collection: str, doc_id: str) -> None:
        self._col(collection).document(doc_id).delete()

    def all(self, collection: str) -> list[dict[str, Any]]:
        return [doc.to_dict() for doc in self._col(collection).stream()]

    def query(self, collection: str, **equals: Any) -> list[dict[str, Any]]:
        query = self._col(collection)
        for key, value in equals.items():
            if isinstance(value, (list, tuple, set)):
                query = query.where(key, "in", list(value))
            else:
                query = query.where(key, "==", value)
        return [doc.to_dict() for doc in query.stream()]


_store: DocumentStore | None = None
_store_lock = threading.Lock()


def get_store(settings: Settings | None = None) -> DocumentStore:
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            settings = settings or get_settings()
            if settings.is_cloud and settings.project_id:
                _store = FirestoreDocumentStore(settings.project_id)
            else:
                _store = JsonDocumentStore(settings.store_dir)
    return _store


def set_store(store: DocumentStore | None) -> None:
    """Tests and the seeder use this. Passing None forces the next get_store() to rebuild."""
    global _store
    with _store_lock:
        _store = store


# Collection names, in one place so a typo is a NameError instead of an empty result set.
ESTATES = "estates"
OBLIGATIONS = "obligations"
CASES = "cases"
PACKETS = "packets"
APPROVALS = "approvals"
INBOUND = "inbound"
OUTBOX = "outbox"
AMENDMENTS = "amendments"

ALL_COLLECTIONS = [
    ESTATES,
    OBLIGATIONS,
    CASES,
    PACKETS,
    APPROVALS,
    INBOUND,
    OUTBOX,
    AMENDMENTS,
]
