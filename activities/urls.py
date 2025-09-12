# activities/urls.py
"""
URL configuration for the activities application.

This module defines all routes related to activities and
enrollments. Each route maps to a class-based view that
handles the corresponding HTTP requests.
"""

from django.urls import path
from .views import ActivityListView, ActivityDetailView, EnrollmentListView, EnrollView

# Application namespace used for reverse lookups
app_name = "activities"

#: URL patterns for the activities application
urlpatterns = [
    # List of activities, filtered in the view if needed
    path("", ActivityListView.as_view(), name="list"),

    # Detail page for a single activity, requires activity ID
    path("<int:pk>/", ActivityDetailView.as_view(), name="detail"),

    # Enrollment endpoint for a specific activity (POST-only)
    path("<int:pk>/inscrire/", EnrollView.as_view(), name="enroll"),

    # List of enrollments for the currently authenticated parent
    path("inscriptions/", EnrollmentListView.as_view(), name="enrollments"),
]
