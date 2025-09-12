# activities/apps.py
"""
Application configuration for the activities module.

This module defines the Django application configuration
for the activities app. It specifies default field behavior
and the application namespace.
"""

from django.apps import AppConfig


class ActivitiesConfig(AppConfig):
    """
    Configuration class for the activities application.

    Attributes
    ----------
    default_auto_field : str
        Specifies the type of primary key field to use for models
        that do not define one explicitly. The default is
        'django.db.models.BigAutoField'.
    name : str
        The full Python path to the application. This value
        is required by Django to identify the app.
    """

    # Specifies the default primary key field type for models
    default_auto_field = "django.db.models.BigAutoField"

    # Defines the name of the Django application
    name = "activities"
