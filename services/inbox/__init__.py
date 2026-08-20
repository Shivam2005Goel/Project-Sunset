"""Inbound plane: Gmail watch -> Pub/Sub -> guardrail screen -> classifier -> FSM."""

from services.inbox.handler import InboundPipeline, deliver_local, get_pipeline, set_pipeline

__all__ = ["InboundPipeline", "deliver_local", "get_pipeline", "set_pipeline"]
