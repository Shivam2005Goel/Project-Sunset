"""Task runner. `python tasks.py <target>`.

The Makefile is the canonical interface and mirrors this file target for target. This
exists because `make` is not installed on a stock Windows machine, and "the judge could
not run it" is a bad way to lose a hackathon.

    python tasks.py setup     install dependencies
    python tasks.py seed      build the fictional estate
    python tasks.py demo      replay six simulated weeks
    python tasks.py smoke     end-to-end assertions
    python tasks.py dev       run the API (and the dashboard if node is installed)
    python tasks.py test      unit + contract
    python tasks.py test-adv  the 40-payload adversarial suite
    python tasks.py test-policy   the structural safety proof
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - a task runner's whole job
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

# Windows consoles default to a legacy codepage; without this, printing a currency
# symbol or an em dash from a task crashes with UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TARGETS: dict[str, str] = {}


def target(help_text: str):
    def decorator(fn):
        TARGETS[fn.__name__.replace("_", "-")] = help_text
        return fn

    return decorator


def run(*args: str, check: bool = True, env: dict[str, str] | None = None) -> int:
    printable = " ".join(args)
    print(f"  $ {printable}")
    result = subprocess.run(  # noqa: S603 - arguments are literals from this file
        list(args), cwd=REPO_ROOT, env={**os.environ, **(env or {})}
    )
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def python(*args: str, **kwargs) -> int:
    return run(sys.executable, *args, **kwargs)


def banner(text: str) -> None:
    print(f"\n  {text}\n  {'-' * len(text)}")


# --- setup ---------------------------------------------------------------------------


@target("install Python and Node dependencies")
def setup() -> None:
    banner("Installing dependencies")
    python("-m", "pip", "install", "-e", ".[dev]")
    if shutil.which("npm") and (REPO_ROOT / "web" / "package.json").exists():
        run("npm", "--prefix", "web", "install")
    else:
        print("  (npm not found - skipping the dashboard; the API and CLI work without it)")


@target("check the toolchain, print the active configuration, probe the model")
def doctor() -> None:
    from packages.core.config import REPO_ROOT, get_settings

    banner("Toolchain")
    print(f"  Python          {sys.version.split()[0]}")
    print(f"  Node            {_version('node') or 'not installed (dashboard unavailable)'}")
    print(f"  gcloud          {_version('gcloud', '--version') or 'not installed (local mode only)'}")
    print(f"  Terraform       {_version('terraform', '-version') or 'not installed (local mode only)'}")

    settings = get_settings()
    dotenv = REPO_ROOT / ".env"

    banner("Configuration")
    print(f"  .env            {'read from ' + str(dotenv) if dotenv.exists() else 'absent (fine - local mode needs nothing)'}")
    # Where each value came from matters: a stale AFTERCARE_MODE=cloud left in a shell
    # silently beats the .env file, and that is a genuinely confusing ten minutes.
    for name in ("AFTERCARE_MODE", "PROJECT_ID", "REGION", "MODEL_FAST", "MODEL_DEEP"):
        value = os.environ.get(name)
        print(f"  {name:<15} {value if value else '(default)'}")
    print(f"  GEMINI_API_KEY  {'set' if settings.gemini_api_key else 'not set'}")
    print(f"  Mode            {settings.mode}")
    print(f"  Data directory  {settings.data_dir}")
    for name in ("runtime", "memory", "registry", "guardrail"):
        print(f"  {name + ' adapter':<15} {settings.effective_adapter(name)}")

    banner("Model")
    _probe_model()
    print()


def _probe_model() -> None:
    """One real call, so a misconfigured project is a two-second discovery."""
    from packages.core.llm import ModelUnavailable, get_llm

    try:
        client = get_llm()
    except ModelUnavailable as exc:
        print(f"  FAIL  could not construct the model client\n\n{_indent(str(exc))}")
        return

    if client.provider_name == "offline":
        print("  ok    offline deterministic planner")
        print("        No model call is made. Output is labelled 'offline-deterministic'")
        print("        everywhere it surfaces, including in the dashboard.")
        return

    try:
        model = client.preflight()
        print(f"  ok    {client.provider_name} reached '{model}'")
    except ModelUnavailable as exc:
        print(f"  FAIL  {client.provider_name} could not reach the model\n\n{_indent(str(exc))}")
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        print(f"  FAIL  {client.provider_name}: {exc}")


def _indent(text: str, prefix: str = "        ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _version(command: str, flag: str = "--version") -> str | None:
    if not shutil.which(command):
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [command, flag], capture_output=True, text=True, timeout=20
        )
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001 - a version probe must never break the runner
        return None


# --- the demo ------------------------------------------------------------------------


@target("build the fictional estate: corpus, playbooks, discovery, fleet, drafts")
def seed() -> None:
    python("-m", "demo.seed", *sys.argv[2:])


@target("replay six simulated weeks of inbound mail at 400x")
def demo() -> None:
    python("-m", "demo.timewarp", "--verbose", *sys.argv[2:])


@target("end-to-end: upload -> discovery -> draft -> approve -> inbound -> close")
def smoke() -> None:
    python("-m", "demo.smoke")


@target("publish the institution playbooks to the registry")
def publish_playbooks() -> None:
    python("-c", "from packages.playbooks import publish_all; print('\\n'.join(publish_all()))")


@target("export the court-facing estate record")
def export_audit() -> None:
    python(
        "-c",
        "from packages.core.audit import export_estate_record; "
        "from packages.core.repos import get_repos; "
        "e = get_repos().estates.current(); "
        "print('\\n'.join(str(p) for p in export_estate_record(e).values()))",
    )


# --- running -------------------------------------------------------------------------


@target("run the API on :8000 (and the dashboard on :3000 if node is installed)")
def dev() -> None:
    banner("Starting Aftercare")
    if shutil.which("npm") and (REPO_ROOT / "web" / "node_modules").exists():
        print("  Dashboard: run `npm --prefix web run dev` in a second terminal (:3000)")
    print("  API: http://localhost:8000/health\n")
    python("-m", "services.api.main")


@target("assert the deployment is healthy (cloud) or the local loop works (local)")
def verify() -> None:
    python("-m", "scripts.verify")


# --- tests ---------------------------------------------------------------------------


@target("unit and contract tests")
def test() -> None:
    python("-m", "pytest", "-q", *sys.argv[2:])


@target("the 40-payload adversarial guardrail suite")
def test_adv() -> None:
    python("-m", "pytest", "-q", "tests/test_adversarial.py", "-v")


@target("prove no outbound path bypasses human approval")
def test_policy() -> None:
    python("-m", "pytest", "-q", "tests/test_policy.py", "-v")


@target("format and lint")
def fmt() -> None:
    if shutil.which("ruff"):
        run("ruff", "format", ".")
        run("ruff", "check", "--fix", ".")
    else:
        print("  ruff not installed - run `python tasks.py setup` first")


# --- cloud ---------------------------------------------------------------------------


@target("deploy to Cloud Run (requires PROJECT_ID and gcloud auth)")
def deploy() -> None:
    project = os.environ.get("PROJECT_ID")
    if not project:
        sys.exit("  PROJECT_ID is not set. See README section 4.")
    python("-m", "scripts.deploy")


@target("tear down all cloud resources")
def destroy() -> None:
    if not os.environ.get("PROJECT_ID"):
        sys.exit("  PROJECT_ID is not set.")
    run("terraform", "-chdir=infra", "destroy", "-auto-approve")


@target("delete all local state (.aftercare)")
def clean() -> None:
    from packages.core.config import get_settings

    data = get_settings().data_dir
    shutil.rmtree(data, ignore_errors=True)
    print(f"  removed {data}")


# --- entrypoint ----------------------------------------------------------------------


def usage() -> None:
    print(__doc__.split("\n\n")[0])
    print("\n  Targets:\n")
    width = max(len(name) for name in TARGETS)
    for name, help_text in TARGETS.items():
        print(f"    {name:<{width}}  {help_text}")
    print()


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "help"}:
        usage()
        return 0

    name = sys.argv[1]
    fn = globals().get(name.replace("-", "_"))
    if name not in TARGETS or fn is None:
        print(f"  unknown target '{name}'\n")
        usage()
        return 1
    fn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
