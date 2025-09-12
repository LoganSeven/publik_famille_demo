# billing/exceptions.py
"""
Custom exceptions for the billing application.

This module defines domain-specific exceptions used for
error handling in billing, payment, PDF generation, and
document storage.
"""


class BillingError(Exception):
    """
    Base class for billing-related errors.

    All billing exceptions should inherit from this class
    to allow grouped exception handling.
    """


class PaymentError(BillingError):
    """
    Raised when a payment operation fails.

    Typically indicates issues such as gateway errors
    or rejected transactions.
    """


class PDFGenerationError(BillingError):
    """
    Raised when invoice PDF generation fails.

    Used when ReportLab or file I/O errors prevent
    successful PDF creation.
    """


class DocumentStorageError(BillingError):
    """
    Raised when storing an invoice document fails.

    Typically occurs when saving the generated PDF
    into the Document model or file system.
    """
