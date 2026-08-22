"""The Review Thread: the audit trail, read back as the conversation that produced it.

Three modules and one idea. `model` is the shape a post has; `projection` builds the
thread out of `audit_events` plus the answers a round produced; `answering` composes a
reply to a person's question out of the same record.

## Why this is in ``attestor_platform``

Same reason ``export`` is. The control plane serves this surface, the control plane
depends on core and platform only, and putting it in ``attestor_fleet`` would pull
google-adk and vertexai into the one service a browser can reach. It is also, like
``export``, an adapter: translating our own records into a shape somebody else reads.

## The property that makes it worth having

Nothing here writes. The thread is a *view* of the compliance record, so it cannot claim
an agent did something the record does not carry, and it cannot go stale relative to a
separate feed because there is no separate feed. The only two events this surface causes
are the ones a person's own question produces -- ``human_asked`` and
``orchestrator_answered`` -- and both are written by the control plane into the same
append-only collection as everything else, because a conversation about a compliance
decision belongs in the compliance record.
"""

from attestor_platform.thread.answering import Composed, answer_from_trail, resolve_question
from attestor_platform.thread.model import (
    Action,
    Detail,
    Post,
    Progress,
    Row,
    Thread,
)
from attestor_platform.thread.projection import build_thread, question_labels

__all__ = [
    "Action",
    "Composed",
    "Detail",
    "Post",
    "Progress",
    "Row",
    "Thread",
    "answer_from_trail",
    "build_thread",
    "question_labels",
    "resolve_question",
]
