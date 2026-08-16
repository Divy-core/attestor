"""Memory Bank — cross-session commitment storage.

Firestore stays the queryable mirror for the UI and the audit trail. Memory Bank is
canonical for what was promised to a customer, because that is the fact that has to
survive weeks and rounds rather than a request.
"""

from attestor_platform.memory.commitments import (
    MemoryBankCommitments,
    decode_fact,
    encode_fact,
)

__all__ = ["MemoryBankCommitments", "decode_fact", "encode_fact"]
