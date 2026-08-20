"""Discovery: obligation-graph reconstruction from the uploaded corpus.

Runs as a Cloud Run Job - one shot per estate, then exits.
"""

from services.discovery.job import DiscoveryJob, run_discovery

__all__ = ["DiscoveryJob", "run_discovery"]
