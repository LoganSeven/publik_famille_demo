# families/urls.py
"""
URL configuration for the families application.

This module defines routes for managing children,
including listing, creating, updating, and deleting.
"""

from django.urls import path
from .views import (
    ChildListView,
    ChildCreateView,
    ChildUpdateView,
    ChildDeleteView,
)

# Application namespace for reverse lookups
app_name = "families"

#: URL patterns for the families application
urlpatterns = [
    # List all children for the authenticated parent
    path("", ChildListView.as_view(), name="child_list"),

    # Add a new child
    path("ajouter/", ChildCreateView.as_view(), name="child_add"),

    # Edit an existing child by primary key
    path("<int:pk>/editer/", ChildUpdateView.as_view(), name="child_edit"),

    # Delete an existing child by primary key
    path("<int:pk>/supprimer/", ChildDeleteView.as_view(), name="child_delete"),
]
