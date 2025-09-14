# documents/apps.py
"""
Application configuration for the documents module.

This module defines the app configuration for documents,
which manages user-uploaded documents and invoice files.
"""

from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    """
    Configuration class for the documents application.

    Attributes
    ----------
    default_auto_field : str
        Default primary key field type for models that do not
        explicitly define one.
    name : str
        Full Python path to the application.
    """

    # Default primary key field type for models
    default_auto_field = "django.db.models.BigAutoField"

    # Application name used by Django to locate the app
    name = "documents"
