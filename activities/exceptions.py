# activities/exceptions.py
class EnrollmentError(Exception):
    """Base class for enrollment-related gateway errors."""


class EnrollmentCreationError(EnrollmentError):
    """Raised when remote enrollment creation fails."""


class EnrollmentSyncError(EnrollmentError):
    """Raised when remote enrollment synchronization fails."""
