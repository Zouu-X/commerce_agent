class AgentError(Exception):
    """Base error exposed through a stable API code."""


class AgentLimitError(AgentError):
    """The model exceeded a configured loop or tool-call budget."""


class AgentTimeoutError(AgentError):
    """The total request, model, or tool timeout was exceeded."""


class ModelProviderError(AgentError):
    """The configured model provider failed or returned an invalid response."""


class ConversationNotFoundError(AgentError):
    """The conversation was absent from the trusted commerce context."""


class InvalidCommerceContextError(AgentError):
    """The trusted identity tuple does not identify a valid demo context."""
