"""Registry adapter - versioned institution playbooks.

Agent Registry when it is available; a versioned object store when it is not. Both
implement the same four operations, which are the four that matter for the reuse
argument: **publish a version, fetch by name and version, list versions, diff two
versions.**

If Agent Registry turns out to be waitlisted on Day 0, nothing above this file changes.
The video line stays true either way: *designed against Agent Registry's contract, with a
portable fallback implementation.*
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

from packages.core.clock import iso
from packages.core.config import Settings, get_settings
from packages.core.logging import get_logger

log = get_logger("adapter.registry")

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionError(ValueError):
    pass


def parse_version(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(version.strip())
    if not match:
        raise VersionError(f"'{version}' is not semantic versioning (major.minor.patch)")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def bump(version: str, level: str = "minor") -> str:
    major, minor, patch = parse_version(version)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


class RegistryAdapter(ABC):
    name = "registry"

    @abstractmethod
    def publish(self, name: str, version: str, document: dict[str, Any], *, notes: str = "") -> str: ...

    @abstractmethod
    def fetch(self, name: str, version: str = "latest") -> dict[str, Any]: ...

    @abstractmethod
    def list_names(self) -> list[str]: ...

    @abstractmethod
    def list_versions(self, name: str) -> list[str]: ...

    def latest_version(self, name: str) -> str | None:
        versions = self.list_versions(name)
        return versions[-1] if versions else None

    def exists(self, name: str, version: str = "latest") -> bool:
        try:
            self.fetch(name, version)
            return True
        except KeyError:
            return False

    def diff(self, name: str, from_version: str, to_version: str) -> list[dict[str, Any]]:
        """Field-level diff between two published versions.

        Shown in the dashboard when a sub-agent proposes an amendment. An executor
        approving a playbook change needs to see the change, not a version number.
        """
        left = self.fetch(name, from_version)
        right = self.fetch(name, to_version)
        return diff_documents(left, right)


def diff_documents(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    skip = {"published_at", "version"}
    for key in sorted(set(left) | set(right)):
        if key in skip:
            continue
        before, after = left.get(key), right.get(key)
        if before == after:
            continue
        if isinstance(before, list) and isinstance(after, list):
            added = [item for item in after if item not in before]
            removed = [item for item in before if item not in after]
            if added or removed:
                changes.append({"field": key, "change": "list", "added": added, "removed": removed})
        else:
            changes.append({"field": key, "change": "set", "from": before, "to": after})
    return changes


class VersionedFileRegistry(RegistryAdapter):
    """Fallback: `<root>/<name>/<version>.yaml` plus an index document.

    This is the GCS-versioned-YAML design from ARCHITECTURE.md section 4, backed by the
    filesystem in local mode. Versions are immutable: republishing an existing version
    raises rather than overwriting, because a registry that lets you rewrite v1.0.0 is
    not a registry.
    """

    name = "gcs_versioned"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        # Published versions are immutable, so a fetched document can be held forever.
        # This matters more than it looks: the orchestrator resolves a playbook on every
        # draft, every inbound letter and every dormancy wake, and without a cache a
        # six-week replay re-parses the whole catalog several hundred times.
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._index_cache: dict[str, Any] | None = None

    def _dir(self, playbook: str) -> Path:
        return self.root / playbook

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _index(self) -> dict[str, Any]:
        if self._index_cache is not None:
            return self._index_cache
        path = self._index_path()
        self._index_cache = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"playbooks": {}}
        )
        return self._index_cache

    def _write_index(self, index: dict[str, Any]) -> None:
        self._index_path().write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
        self._index_cache = index

    def publish(self, name: str, version: str, document: dict[str, Any], *, notes: str = "") -> str:
        parse_version(version)
        target = self._dir(name) / f"{version}.yaml"
        if target.exists():
            raise VersionError(
                f"{name}@{version} already published. Versions are immutable - bump instead."
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {**document, "name": name, "version": version, "published_at": iso()}
        target.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")

        index = self._index()
        entry = index["playbooks"].setdefault(name, {"versions": [], "notes": {}})
        entry["versions"] = sorted({*entry["versions"], version}, key=parse_version)
        entry["notes"][version] = notes
        entry["latest"] = entry["versions"][-1]
        entry["display_name"] = document.get("institution_name", name)
        entry["category"] = document.get("category", "OTHER")
        self._write_index(index)

        self._cache[(name, version)] = payload

        log.info("playbook.published", playbook=name, version=version)
        return f"{name}@{version}"

    def fetch(self, name: str, version: str = "latest") -> dict[str, Any]:
        if version == "latest":
            resolved = self.latest_version(name)
            if resolved is None:
                raise KeyError(f"playbook '{name}' has no published versions")
            version = resolved
        cached = self._cache.get((name, version))
        if cached is not None:
            return dict(cached)

        path = self._dir(name) / f"{version}.yaml"
        if not path.exists():
            raise KeyError(f"playbook '{name}@{version}' not found in registry")
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        self._cache[(name, version)] = document
        return dict(document)

    def list_names(self) -> list[str]:
        return sorted(self._index()["playbooks"].keys())

    def list_versions(self, name: str) -> list[str]:
        entry = self._index()["playbooks"].get(name)
        if not entry:
            return []
        return sorted(entry["versions"], key=parse_version)

    def catalog(self) -> list[dict[str, Any]]:
        """What the dashboard's registry panel renders."""
        index = self._index()["playbooks"]
        return [
            {
                "name": name,
                "display_name": entry.get("display_name", name),
                "category": entry.get("category", "OTHER"),
                "latest": entry.get("latest"),
                "versions": entry.get("versions", []),
                "notes": entry.get("notes", {}),
            }
            for name, entry in sorted(index.items())
        ]


class GcsVersionedRegistry(VersionedFileRegistry):  # pragma: no cover - cloud only
    """Same layout, backed by a GCS bucket. Object versioning is on in `infra/storage.tf`."""

    name = "gcs_versioned_cloud"

    def __init__(self, bucket: str, prefix: str = "playbooks", cache_dir: Path | None = None) -> None:
        from google.cloud import storage

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._prefix = prefix
        super().__init__(cache_dir or Path(".aftercare/registry-cache"))

    def publish(self, name: str, version: str, document: dict[str, Any], *, notes: str = "") -> str:
        ref = super().publish(name, version, document, notes=notes)
        local = self._dir(name) / f"{version}.yaml"
        self._bucket.blob(f"{self._prefix}/{name}/{version}.yaml").upload_from_filename(str(local))
        self._bucket.blob(f"{self._prefix}/index.json").upload_from_filename(str(self._index_path()))
        return ref

    def fetch(self, name: str, version: str = "latest") -> dict[str, Any]:
        try:
            return super().fetch(name, version)
        except KeyError:
            blob = self._bucket.blob(f"{self._prefix}/{name}/{version}.yaml")
            if not blob.exists():
                raise
            return yaml.safe_load(blob.download_as_text())


class AgentRegistryAdapter(RegistryAdapter):  # pragma: no cover - GEAP, unverified
    """Google Agent Registry.

    Written against the documented contract and **never executed against the live
    service** - see the honesty note in CLAUDE.md. If the surface differs, fix it here
    and nowhere else. The constructor deliberately fails loudly rather than degrading
    silently; `get_registry_adapter` decides whether to fall back.
    """

    name = "agent_registry"

    def __init__(self, project_id: str, location: str) -> None:
        self._project = project_id
        self._location = location
        try:
            from google.cloud import aiplatform_v1beta1  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Agent Registry SDK not installed. Install the 'cloud' extra, or set "
                "REGISTRY_ADAPTER=gcs_versioned to use the fallback."
            ) from exc
        raise RuntimeError(
            "Agent Registry client is not wired yet - Day 0 must confirm the service is "
            "GA in this region before this path is trusted. Set REGISTRY_ADAPTER="
            "gcs_versioned meanwhile; it satisfies the same contract."
        )

    def publish(self, name, version, document, *, notes=""): ...
    def fetch(self, name, version="latest"): ...
    def list_names(self): ...
    def list_versions(self, name): ...


_adapter: RegistryAdapter | None = None


def get_registry_adapter(settings: Settings | None = None, *, force_new: bool = False) -> RegistryAdapter:
    global _adapter
    if _adapter is not None and not force_new:
        return _adapter
    settings = settings or get_settings()
    choice = settings.effective_adapter("registry")

    if choice == "agent_registry":  # pragma: no cover - cloud only
        try:
            _adapter = AgentRegistryAdapter(settings.project_id or "", settings.region)
            return _adapter
        except RuntimeError as exc:
            log.warning("registry.fallback", reason=str(exc))
            choice = "gcs_versioned"

    if choice == "gcs_versioned" and settings.is_cloud and settings.project_id:  # pragma: no cover
        _adapter = GcsVersionedRegistry(
            bucket=f"{settings.project_id}-aftercare-registry", cache_dir=settings.registry_dir
        )
    else:
        _adapter = VersionedFileRegistry(settings.registry_dir)
    return _adapter


def set_registry_adapter(adapter: RegistryAdapter | None) -> None:
    global _adapter
    _adapter = adapter
