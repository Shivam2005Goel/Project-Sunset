"""`make deploy` - build, push and deploy the five services to Cloud Run.

Never executed against a live project as of this commit. Day 10 in BUILD_PLAN.md exists
precisely to run this against a **brand-new** GCP project, because deploying only from a
laptop whose project is already configured proves nothing about reproducibility - and
that is 30% of the grade.

Read this before Day 10: everything below assumes `terraform apply` has already run, and
that the service accounts named in `infra/iam.tf` exist.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - a deploy script shells out; that is the job
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICES = {
    # name: (dockerfile target, entrypoint module, min instances)
    "api": ("api", "services.api.main", 0),
    "orchestrator": ("orchestrator", "services.orchestrator.root", 0),
    "inbox": ("inbox", "services.inbox.handler", 0),
    "worker": ("worker", "services.worker.agent", 0),
}
JOBS = {"discovery": ("discovery", "services.discovery.job")}


def sh(*args: str, check: bool = True) -> str:
    print(f"  $ {' '.join(args)}")
    result = subprocess.run(  # noqa: S603
        list(args), cwd=REPO_ROOT, text=True, capture_output=True
    )
    if result.stdout.strip():
        print("    " + result.stdout.strip().replace("\n", "\n    "))
    if check and result.returncode != 0:
        print("    " + (result.stderr or "").strip().replace("\n", "\n    "))
        sys.exit(result.returncode)
    return result.stdout


def main() -> int:
    project = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")
    if not project:
        sys.exit("PROJECT_ID is not set. See README section 4.")
    if not shutil.which("gcloud"):
        sys.exit("gcloud is not installed. See README section 2.")

    repo = f"{region}-docker.pkg.dev/{project}/aftercare"
    print(f"\n  Deploying Aftercare to {project} ({region})\n")

    sh("gcloud", "auth", "configure-docker", f"{region}-docker.pkg.dev", "--quiet")

    for name, (dockerfile_target, module, min_instances) in SERVICES.items():
        image = f"{repo}/{name}:latest"
        sh("docker", "build", "--target", dockerfile_target, "-t", image, ".")
        sh("docker", "push", image)
        sh(
            "gcloud", "run", "deploy", f"aftercare-{name}",
            "--image", image,
            "--region", region,
            "--platform", "managed",
            "--service-account", f"aftercare-{name}@{project}.iam.gserviceaccount.com",
            "--set-env-vars", f"AFTERCARE_MODE=cloud,PROJECT_ID={project},REGION={region}",
            "--min-instances", str(min_instances),
            # Cost control: README section 8. Three instances is enough for a demo and
            # cheap enough to leave running through judging.
            "--max-instances", "3",
            "--memory", "512Mi",
            "--cpu", "1",
            "--no-allow-unauthenticated" if name != "api" else "--allow-unauthenticated",
            "--quiet",
            "--command", "python", "--args", f"-m,{module}",
        )

    for name, (dockerfile_target, module) in JOBS.items():
        image = f"{repo}/{name}:latest"
        sh("docker", "build", "--target", dockerfile_target, "-t", image, ".")
        sh("docker", "push", image)
        sh(
            "gcloud", "run", "jobs", "deploy", f"aftercare-{name}",
            "--image", image,
            "--region", region,
            "--service-account", f"aftercare-{name}@{project}.iam.gserviceaccount.com",
            "--set-env-vars", f"AFTERCARE_MODE=cloud,PROJECT_ID={project},REGION={region}",
            "--max-retries", "1",
            "--quiet",
            "--command", "python", "--args", f"-m,{module}",
        )

    if (REPO_ROOT / "web" / "package.json").exists():
        image = f"{repo}/web:latest"
        sh("docker", "build", "-t", image, "web")
        sh("docker", "push", image)
        sh(
            "gcloud", "run", "deploy", "aftercare-web",
            "--image", image, "--region", region, "--platform", "managed",
            "--allow-unauthenticated", "--min-instances", "0", "--max-instances", "3",
            "--quiet",
        )

    url = sh(
        "gcloud", "run", "services", "describe", "aftercare-web",
        "--region", region, "--format", "value(status.url)",
        check=False,
    ).strip()

    print("\n  Deployed. Next:")
    print("    python tasks.py publish-playbooks")
    print("    python tasks.py verify")
    if url:
        print(f"\n  Dashboard: {url}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
