# accounts/admin.py
"""
Admin configuration for the accounts application.

This module customizes the Django admin interface for the
:class:`UserProfile` model, enabling list displays, filters,
and search capabilities.
"""

from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """
    Admin configuration for the UserProfile model.

    Defines how user profiles are displayed and managed
    within the Django admin interface.

    Attributes
    ----------
    list_display : tuple
        Fields shown in the admin list view. Here, the
        associated user and the verification flag.
    list_filter : tuple
        Fields usable as filters in the right sidebar.
        Includes the verification status.
    search_fields : tuple
        Fields indexed by the admin search bar. Supports
        searching by username and email of the user.
    """

    # Fields displayed in the admin list view
    list_display = ("user", "id_verified")

    # Filters available in the right sidebar
    list_filter = ("id_verified",)

    # Fields available in the search bar
    search_fields = ("user__username", "user__email")
