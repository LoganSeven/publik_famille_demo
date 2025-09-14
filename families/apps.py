# families/apps.py
"""
Application configuration for the families module.

This module defines the app configuration for the families
application, which manages children and related data.
"""

from django.apps import AppConfig


class FamiliesConfig(AppConfig):
    """
    Configuration class for the families application.

    Attributes
    ----------
    default_auto_field : str
        Default primary key field type for models that do not
        explicitly define one.
    name : str
        Full Python path to the application.
    """

    # Default primary key field type
    default_auto_field = "django.db.models.BigAutoField"

    # Application name used by Django to locate the app
    name = "families"
