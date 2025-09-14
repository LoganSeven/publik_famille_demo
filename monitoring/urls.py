# monitoring/urls.py
"""
URL configuration for the monitoring application.

This module defines routes for accessing monitoring features,
including log visualization for staff users.
"""

from django.urls import path
from .views import logs_view

# Application namespace for reverse lookups
app_name = "monitoring"

#: URL patterns for the monitoring application
urlpatterns = [
    # Display application logs (restricted to staff members)
    path("logs/", logs_view, name="logs"),
]
