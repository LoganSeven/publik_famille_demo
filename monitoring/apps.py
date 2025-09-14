# monitoring/apps.py
"""
Application configuration for the monitoring module.

This module defines the app configuration for monitoring,
which provides logging and system visibility features.
"""

from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    """
    Configuration class for the monitoring application.

    Attributes
    ----------
    default_auto_field : str
        Default primary key field type for models that do not
        explicitly define one.
    name : str
        Full Python path to the monitoring application.
    """

    # Default primary key field type for models
    default_auto_field = "django.db.models.BigAutoField"

    # Application name used by Django to locate the app
    name = "monitoring"
