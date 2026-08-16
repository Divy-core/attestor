"""Agent Registry read API.

The registry is free: agents deployed to Agent Runtime are catalogued automatically.
We do not build a catalogue, we read the real one -- which is why the `/registry` page
can honestly claim to show the live platform registry rather than a mock.

Measured in Phase 0: `v1` and `v1alpha` both serve this; `v1beta1` returns HTTP 404.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

import google.auth
import google.auth.transport.requests

from attestor_core.domain import Department
from attestor_core.errors import ContextUnavailable
from attestor_core.protocol import RegistryAgentDto
from attestor_platform.config import default_region, project_id

logger = logging.getLogger(__name__)

REGISTRY_HOST = "https://agentregistry.googleapis.com"
API_VERSION = "v1"
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Department ownership is ours, not the platform's -- the registry knows nothing about
#: our org chart. Layered on top by display-name convention.
_NAME_DEPARTMENTS: dict[str, Department] = {
    "security": Department.SECURITY,
    "legal": Department.LEGAL,
    "engineering": Department.ENGINEERING,
    "intake": Department.ENGINEERING,
}


def _department_for(display_name: str) -> Department:
    lowered = display_name.lower()
    for token, dept in _NAME_DEPARTMENTS.items():
        if token in lowered:
            return dept
    return Department.UNASSIGNED


class AgentRegistry:
    """Read-only client over the Agent Registry."""

    def __init__(
        self,
        project: str | None = None,
        region: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.project = project or project_id()
        self.region = region or default_region()
        self._timeout = timeout
        self._credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

    def _token(self) -> str:
        if not self._credentials.valid:
            self._credentials.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]
        return str(self._credentials.token)

    def list_agents(self, page_size: int = 100) -> list[RegistryAgentDto]:
        """Return every agent the platform has catalogued for this project.

        An unreachable registry **raises**. It previously returned `[]`, which reads as
        "this project has catalogued no agents" -- and that is a claim, not an absence.
        The registry panel would have rendered empty during a demo that says the fleet is
        registered, and nothing would have said why.

        Raises:
            ContextUnavailable: If the registry could not be read.
        """
        url = (
            f"{REGISTRY_HOST}/{API_VERSION}/projects/{self.project}"
            f"/locations/{self.region}/agents?pageSize={page_size}"
        )
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token()}"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, OSError) as exc:
            raise ContextUnavailable(
                f"agent registry unreachable at {REGISTRY_HOST}: {type(exc).__name__}: {exc}",
                project=self.project,
                region=self.region,
            ) from exc

        agents: list[RegistryAgentDto] = []
        for entry in payload.get("agents", []):
            display = str(entry.get("displayName") or entry.get("name", "").rsplit("/", 1)[-1])
            agents.append(
                RegistryAgentDto(
                    agent_id=str(entry.get("agentId") or entry.get("name", "")),
                    display_name=display,
                    resource_name=entry.get("name"),
                    department=_department_for(display),
                )
            )
        return agents
