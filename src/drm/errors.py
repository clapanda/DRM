class DRMError(Exception):
    """Base error for expected DRM failures."""


class PlanConflictError(DRMError):
    """A change target is ambiguous or missing."""


class SafetyError(DRMError):
    """A safety boundary would be violated."""


class UnsupportedEditError(DRMError):
    """An edit cannot be performed without unacceptable data-loss risk."""
