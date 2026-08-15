"""Model Armor: sanitize client plus the long-text chunker.

`platform.armor` OBTAINS the verdict; `core.policy` DECIDES on it. That split is what
makes every policy branch testable without ever calling the service.
"""

from attestor_platform.armor.client import (
    CHUNK_TOKENS,
    INGRESS_TEMPLATE,
    INJECTION_TOKEN_LIMIT,
    OVERLAP_TOKENS,
    ArmorClient,
    ChunkVerdict,
    LongTextVerdict,
    chunk_text,
    parse_sanitize_response,
)

__all__ = [
    "CHUNK_TOKENS",
    "INGRESS_TEMPLATE",
    "INJECTION_TOKEN_LIMIT",
    "OVERLAP_TOKENS",
    "ArmorClient",
    "ChunkVerdict",
    "LongTextVerdict",
    "chunk_text",
    "parse_sanitize_response",
]
