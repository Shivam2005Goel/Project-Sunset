"""The per-institution sub-agent. Dormant between letters, woken by mail or by a timer."""

from services.worker.agent import InstitutionAgent, get_agent, register, set_agent

__all__ = ["InstitutionAgent", "get_agent", "register", "set_agent"]
