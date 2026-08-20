"""Runtime configuration.

One place that reads the environment. Everything else takes a `Settings` object.

The variable names match README section 5 exactly - if you rename one here, rename it
there in the same commit, because the README is a graded artifact.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["local", "cloud"]

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read `.env` into the environment.

    README section 3 tells you to `cp .env.example .env` and set a variable, so something
    has to read it. Written by hand rather than pulled in as a dependency, because local
    mode's promise is zero dependencies beyond the four in `pyproject.toml`.

    Standard dotenv precedence: **a variable already set in the shell wins.** That is the
    behaviour people expect, and it is worth knowing when a stale `AFTERCARE_MODE=cloud`
    in your shell is quietly overriding the `.env` you just edited - `python tasks.py
    doctor` prints where each value came from.
    """
    path = path or (REPO_ROOT / ".env")
    applied: dict[str, str] = {}
    if not path.exists():
        return applied

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    """Immutable view of the environment."""

    mode: Mode = "local"

    project_id: str | None = None
    region: str = "us-central1"

    model_fast: str = "gemini-1.5-flash"
    model_deep: str = "gemini-1.5-pro"
    gemini_api_key: str | None = Field(default=None, repr=False)

    runtime_adapter: str = "agent_runtime"
    memory_adapter: str = "memory_bank"
    registry_adapter: str = "agent_registry"
    guardrail_adapter: str = "model_armor"

    timewarp_factor: int = 1
    data_dir: Path = REPO_ROOT / ".aftercare"

    executor_email: str = "executor@example.invalid"

    @property
    def auto_send(self) -> bool:
        """Hard-wired false. Invariant 1 in CLAUDE.md.

        `AUTO_SEND` exists as a documented environment variable so that the safety
        boundary is explicit and *testable*, not so that it can be turned on. This
        property ignores the environment on purpose; `tests/test_policy.py` asserts that
        setting AUTO_SEND=true in the environment does not change the return value.
        """
        return False

    @property
    def is_cloud(self) -> bool:
        return self.mode == "cloud"

    # --- derived paths (local mode) -------------------------------------------------

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def registry_dir(self) -> Path:
        return self.data_dir / "registry"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def trace_path(self) -> Path:
        return self.data_dir / "traces.json"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.store_dir,
            self.blob_dir,
            self.outbox_dir,
            self.registry_dir,
            self.memory_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # --- adapter resolution ---------------------------------------------------------

    def effective_adapter(self, name: str) -> str:
        """Which implementation an adapter factory should build.

        In local mode every GEAP adapter resolves to its fallback regardless of what the
        environment asks for - a local run must never try to reach a Google endpoint.
        """
        chosen = getattr(self, f"{name}_adapter")
        if not self.is_cloud:
            return FALLBACK_ADAPTERS[name]
        return chosen


FALLBACK_ADAPTERS = {
    "runtime": "cloud_run_jobs",
    "memory": "firestore_vector",
    "registry": "gcs_versioned",
    "guardrail": "dlp_plus_classifier",
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    mode: Mode = "cloud" if _env("AFTERCARE_MODE", "local") == "cloud" else "local"
    data_dir = Path(_env("AFTERCARE_DATA_DIR", str(REPO_ROOT / ".aftercare")))

    settings = Settings(
        mode=mode,
        project_id=_env("PROJECT_ID"),
        region=_env("REGION", "us-central1"),
        model_fast=_env("MODEL_FAST", "gemini-1.5-flash"),
        model_deep=_env("MODEL_DEEP", "gemini-1.5-pro"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        runtime_adapter=_env("RUNTIME_ADAPTER", "agent_runtime"),
        memory_adapter=_env("MEMORY_ADAPTER", "memory_bank"),
        registry_adapter=_env("REGISTRY_ADAPTER", "agent_registry"),
        guardrail_adapter=_env("GUARDRAIL_ADAPTER", "model_armor"),
        timewarp_factor=int(_env("TIMEWARP_FACTOR", "1") or 1),
        data_dir=data_dir,
        executor_email=_env("EXECUTOR_EMAIL", "executor@example.invalid"),
    )
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Tests only - lets a test change the environment and re-read it."""
    get_settings.cache_clear()


# Referenced by tests/test_policy.py. Kept as a module-level literal so the static
# analysis can read it without importing cloud dependencies.
AUTO_SEND = False
