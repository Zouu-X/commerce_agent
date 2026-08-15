class ApprovalError(Exception):
    """Base class for stable approval workflow errors."""


class ApprovalNotFoundError(ApprovalError):
    """Raised when an action is outside the approver's trusted scope."""


class InvalidActionTransitionError(ApprovalError):
    """Raised when a terminal or executing action receives an invalid transition."""


class ActionValidationError(ApprovalError):
    """Raised when an action is not eligible at request or execution time."""


class ActionExecutionError(ApprovalError):
    """Internal stable failure used to persist a failed execution outcome."""
