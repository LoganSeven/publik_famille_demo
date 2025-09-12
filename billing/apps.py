# billing/apps.py
"""
Application configuration for the billing module.

This module defines the app configuration for billing,
ensuring signals are registered when the application is ready.
"""

from django.apps import AppConfig


class BillingConfig(AppConfig):
    """
    Configuration class for the billing application.

    Attributes
    ----------
    default_auto_field : str
        Default primary key field type for models that do not
        explicitly define one.
    name : str
        Full Python path to the application.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        """
        Initialize the billing application.

        Ensures that signals are imported and connected when
        the application is loaded by Django.
        """
        from . import signals  # noqa: F401
