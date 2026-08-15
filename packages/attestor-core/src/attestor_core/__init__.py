"""Attestor domain core.

Pure Python: stdlib + pydantic only. No cloud imports, no network, no I/O.
`tools/check_layering.py` enforces that mechanically.
"""

__all__ = ["domain", "errors", "policy", "protocol", "state"]
