"""Stable application-facing outbound A2A failure taxonomy."""


class OutboundA2AError(RuntimeError):
    """Base error for remote-agent operations."""


class AgentDiscoveryError(OutboundA2AError):
    """Discovery or card verification failed."""


class UnsupportedProtocolError(OutboundA2AError):
    """Agent offers no compatible A2A binding."""


class RemoteAgentUnavailable(OutboundA2AError):
    """Remote agent could not be contacted."""


class AgentAuthenticationError(OutboundA2AError):
    """Remote agent denied the caller."""


class TaskExecutionError(OutboundA2AError):
    """Remote task failed or returned an invalid result."""
