# activities/urls.py
from django.urls import path
from .views import ActivityListView, ActivityDetailView, EnrollmentListView, EnrollView

# Explicitly name the app for URL namespacing
app_name = "activities"

# URL patterns for activity-related views
urlpatterns = [
    # List all active activities
    path("", ActivityListView.as_view(), name="list"),
    # Detail view for a single activity (identified by primary key)
    path("<int:pk>/", ActivityDetailView.as_view(), name="detail"),
    # Endpoint to enroll a child in an activity (POST only)
    path("<int:pk>/inscrire/", EnrollView.as_view(), name="enroll"),
    # List all enrollments for the authenticated user
    path("inscriptions/", EnrollmentListView.as_view(), name="enrollments"),
]
