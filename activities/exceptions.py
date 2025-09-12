# activities/exceptions.py
"""
Custom exceptions for the activities application.

This module defines domain-specific exceptions used for handling
errors related to the enrollment process. These exceptions provide
a structured way to signal and catch gateway-related failures.
"""


class EnrollmentError(Exception):
    """
    Base class for enrollment-related errors.

    All custom exceptions related to enrollment inherit from
    this class. It can be used to catch any enrollment-specific
    error raised by the application.
    """


class EnrollmentCreationError(EnrollmentError):
    """
    Raised when remote enrollment creation fails.

    Typically used when the gateway or backend service cannot
    process a new enrollment request successfully.
    """


class EnrollmentSyncError(EnrollmentError):
    """
    Raised when remote enrollment synchronization fails.

    Typically used when the local enrollment state cannot
    be synchronized with the remote gateway or backend service.
    """
