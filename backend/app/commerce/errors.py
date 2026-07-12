class CommerceError(Exception):
    """Base class for safe domain errors."""


class ResourceNotFoundError(CommerceError):
    """The resource does not exist inside the trusted commerce context."""
