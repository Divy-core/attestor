"""Telemetry: OTel span helpers plus the append-only audit writers."""

from attestor_platform.telemetry.audit import (
    ArmorEventWriter,
    AuditWriter,
    current_trace_id,
    span,
)

__all__ = ["ArmorEventWriter", "AuditWriter", "current_trace_id", "span"]
