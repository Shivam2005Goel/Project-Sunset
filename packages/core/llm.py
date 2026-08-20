"""Model routing.

Every model call in the codebase goes through `LLMClient.complete`. That is where
routing, retries, token accounting and tracing live; `tests/test_policy.py` fails the
build if `genai` or `vertexai` is imported anywhere outside this file and
`packages/core/adapters/`.

Three providers:

* `vertex`  - Gemini on Vertex AI. The submission target.
* `gemini`  - Gemini Developer API, for a laptop with only an API key.
* `offline` - a deterministic planner. **Not a model.** It is rule-based code that
  satisfies the same task contracts so the full agent loop runs with no network. Its
  responses are labelled `offline-deterministic` everywhere they surface, including in
  the dashboard, because a demo that quietly passes off rules as model output is exactly
  the kind of thing that loses a hackathon.

Task names are the routing key. Adding a task means adding an offline handler for it -
`tests/test_llm.py` enforces that, so local mode can never silently lose a capability.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from packages.core.config import Settings, get_settings
from packages.core.logging import get_logger
from packages.core.telemetry import span

log = get_logger("llm")

OFFLINE_MODEL = "offline-deterministic"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    text: str
    model: str
    task: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_offline(self) -> bool:
        return self.model == OFFLINE_MODEL


def approx_tokens(text: str) -> int:
    """Cheap and consistent. Good enough for a budget alarm, not for billing."""
    return max(1, len(text) // 4)


class ModelUnavailable(RuntimeError):
    """The configured model cannot be reached, and retrying will not help.

    A 404 for a publisher model or a 403 on the project is a configuration fact, not a
    transient fault. Retrying it three times and then printing a sixty-line SDK traceback
    tells you nothing you can act on, so this carries the remediation instead.

    Deliberately *not* a silent fall back to the offline planner. In cloud mode the whole
    claim is that Gemini is doing the reasoning; degrading to rules without saying so
    would make the demo a lie. Degrading to rules *with* saying so is what local mode is,
    and it is one environment variable away.
    """

    def __init__(self, *, settings: Settings, model: str, provider: str, cause: Exception) -> None:
        detail = str(cause).strip().replace("\n", " ")[:300]
        super().__init__(
            f"Cannot reach model '{model}' via {provider} "
            f"(project={settings.project_id or 'unset'}, region={settings.region}).\n\n"
            f"  What the API said: {detail}\n\n"
            f"  Three ways forward, fastest first:\n\n"
            f"    1. Run locally. Nothing else is required:\n"
            f"         AFTERCARE_MODE=local python tasks.py seed\n"
            f"       Every code path is the same; the reasoning is done by the offline\n"
            f"       planner and labelled 'offline-deterministic' everywhere it surfaces.\n\n"
            f"    2. Use a Gemini API key instead of a GCP project:\n"
            f"         AFTERCARE_MODE=local GEMINI_API_KEY=<key> python tasks.py seed\n"
            f"       Get one at https://aistudio.google.com/apikey\n\n"
            f"    3. Fix the cloud configuration. Check that PROJECT_ID names a real\n"
            f"       project with billing and Vertex AI enabled, that REGION serves this\n"
            f"       model, and that MODEL_FAST/MODEL_DEEP name models the project can\n"
            f"       call. Day 0 in BUILD_PLAN.md exists to settle exactly this.\n\n"
            f"  `python tasks.py doctor` prints the active configuration."
        )
        self.cause = cause
        self.model = model


# HTTP statuses and SDK phrases that mean "your configuration is wrong", as opposed to
# "the service is briefly unhappy". Retrying the first kind just wastes the user's time.
_FATAL_MARKERS = (
    "404",
    "not_found",
    "403",
    "permission_denied",
    "401",
    "unauthenticated",
    "invalid_argument",
    "was not found or your project does not have access",
    "api key not valid",
    "billing",
)


def is_configuration_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status in {400, 401, 403, 404}:
        return True
    return any(marker in text for marker in _FATAL_MARKERS)


class Provider:
    name = "provider"

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        task: str,
        inputs: dict[str, Any],
        images: list[bytes] | None = None,
        json_mode: bool = True,
    ) -> tuple[str, dict[str, Any]]:  # pragma: no cover - interface
        raise NotImplementedError


class OfflineProvider(Provider):
    """Deterministic task handlers. Registered in `packages/core/offline.py`."""

    name = "offline"

    def generate(self, *, model, prompt, task, inputs, images=None, json_mode=True):
        from packages.core import offline

        handler = offline.HANDLERS.get(task)
        if handler is None:
            raise LookupError(
                f"No offline handler for task '{task}'. Local mode must support every "
                f"task; add one in packages/core/offline.py."
            )
        data = handler(inputs)
        return json.dumps(data, default=str), data


class VertexProvider(Provider):  # pragma: no cover - cloud only
    """Gemini on Vertex AI via google-genai."""

    name = "vertex"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        self._client = genai.Client(
            vertexai=True, project=settings.project_id, location=settings.region
        )

    def generate(self, *, model, prompt, task, inputs, images=None, json_mode=True):
        from google.genai import types

        parts: list[Any] = [types.Part.from_text(text=prompt)]
        for blob in images or []:
            parts.append(types.Part.from_bytes(data=blob, mime_type="image/jpeg"))

        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        result = self._client.models.generate_content(
            model=model, contents=[types.Content(role="user", parts=parts)], config=config
        )
        text = result.text or ""
        return text, _loose_json(text) if json_mode else {}


class GeminiApiProvider(Provider):  # pragma: no cover - needs an API key
    """Gemini Developer API - the `GEMINI_API_KEY` path from README section 3."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate(self, *, model, prompt, task, inputs, images=None, json_mode=True):
        from google.genai import types

        parts: list[Any] = [types.Part.from_text(text=prompt)]
        for blob in images or []:
            parts.append(types.Part.from_bytes(data=blob, mime_type="image/jpeg"))
        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        result = self._client.models.generate_content(
            model=model, contents=[types.Content(role="user", parts=parts)], config=config
        )
        text = result.text or ""
        return text, _loose_json(text) if json_mode else {}


def _loose_json(text: str) -> dict[str, Any]:
    """Models sometimes fence their JSON. Take the first object that parses."""
    text = text.strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {"raw": text}


# Tasks that justify the expensive model. Keep this list short and say why - the whole
# cost story is "Flash-first, escalate deliberately".
DEEP_TASKS = {
    # Closure-packet synthesis is the one place where a weak draft costs the executor
    # real time, because they read every word of it before approving.
    "packet.draft",
}


class LLMClient:
    def __init__(self, settings: Settings | None = None, provider: Provider | None = None) -> None:
        self._settings = settings or get_settings()
        self._provider = provider or _build_provider(self._settings)
        self._lock = threading.Lock()
        self.calls: int = 0
        self.usage = Usage()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def model_for(self, task: str, deep: bool | None = None) -> str:
        if isinstance(self._provider, OfflineProvider):
            return OFFLINE_MODEL
        use_deep = DEEP_TASKS.__contains__(task) if deep is None else deep
        return self._settings.model_deep if use_deep else self._settings.model_fast

    def preflight(self) -> str:
        """One cheap call to prove the model is reachable before doing forty-four.

        Called by `seed`, `doctor` and `verify`. Discovering a misconfigured project on
        call 1 costs two seconds; discovering it on call 12 costs a confusing partial
        estate and a traceback.
        """
        if isinstance(self._provider, OfflineProvider):
            return OFFLINE_MODEL
        response = self.complete(
            "guardrail.judge",
            prompt=(
                'Reply with exactly this JSON and nothing else: {"verdict": "ALLOW", '
                '"rationale": "preflight"}'
            ),
            inputs={"text": ""},
            retries=0,
        )
        return response.model

    def complete(
        self,
        task: str,
        *,
        prompt: str,
        inputs: dict[str, Any] | None = None,
        images: list[bytes] | None = None,
        deep: bool | None = None,
        json_mode: bool = True,
        retries: int = 2,
    ) -> LLMResponse:
        inputs = inputs or {}
        model = self.model_for(task, deep)
        started = time.perf_counter()

        with span("llm.complete", task=task, model=model, provider=self._provider.name) as sp:
            last_error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    text, data = self._provider.generate(
                        model=model,
                        prompt=prompt,
                        task=task,
                        inputs=inputs,
                        images=images,
                        json_mode=json_mode,
                    )
                    break
                except LookupError:
                    raise
                except Exception as exc:  # noqa: BLE001 - classified, then surfaced
                    last_error = exc
                    if is_configuration_error(exc):
                        # Not transient. Fail now with something the reader can act on.
                        sp.attributes["error"] = "configuration"
                        raise ModelUnavailable(
                            settings=self._settings,
                            model=model,
                            provider=self._provider.name,
                            cause=exc,
                        ) from exc
                    if attempt == retries:
                        sp.attributes["error"] = str(exc)
                        raise
                    time.sleep(0.25 * (attempt + 1))
            else:  # pragma: no cover - unreachable, the loop either breaks or raises
                raise RuntimeError(str(last_error))

            usage = Usage(
                prompt_tokens=approx_tokens(prompt) + 258 * len(images or []),
                completion_tokens=approx_tokens(text),
            )
            latency = (time.perf_counter() - started) * 1000
            sp.attributes.update(
                {"tokens": usage.total, "latency_ms": round(latency, 1), "images": len(images or [])}
            )

        with self._lock:
            self.calls += 1
            self.usage.prompt_tokens += usage.prompt_tokens
            self.usage.completion_tokens += usage.completion_tokens

        log.info("llm.complete", task=task, model=model, tokens=usage.total)
        return LLMResponse(
            text=text, model=model, task=task, usage=usage, latency_ms=latency, data=data
        )


def _build_provider(settings: Settings) -> Provider:
    if settings.is_cloud and settings.project_id:  # pragma: no cover - cloud only
        try:
            return VertexProvider(settings)
        except Exception as exc:  # noqa: BLE001 - surfaced with remediation below
            raise ModelUnavailable(
                settings=settings, model=settings.model_fast, provider="vertex", cause=exc
            ) from exc
    if settings.gemini_api_key:  # pragma: no cover - needs a key
        try:
            return GeminiApiProvider(settings)
        except Exception as exc:  # noqa: BLE001
            raise ModelUnavailable(
                settings=settings, model=settings.model_fast, provider="gemini", cause=exc
            ) from exc
    return OfflineProvider()


_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm() -> LLMClient:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = LLMClient()
    return _client


def set_llm(client: LLMClient | None) -> None:
    global _client
    with _client_lock:
        _client = client


HandlerType = Callable[[dict[str, Any]], dict[str, Any]]
