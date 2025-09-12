# activities/admin.py
"""
Admin configuration for the activities application.

This module defines Django admin customizations for the
:class:`Activity` and :class:`Enrollment` models. The configuration
controls how these models are displayed, filtered, and searched
in the Django admin interface.
"""

from django.contrib import admin
from .models import Activity, Enrollment


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Activity model.

    Provides list display, filters, and search options for
    Activity records in the Django admin interface.
    """
    # Fields displayed in the admin list view
    list_display = ("title", "fee", "start_date", "end_date", "capacity", "is_active")
    # Filters available in the right sidebar
    list_filter = ("is_active",)
    # Fields available for the admin search bar
    search_fields = ("title",)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Enrollment model.

    Provides list display, filters, and search options for
    Enrollment records in the Django admin interface.
    """
    # Fields displayed in the admin list view
    list_display = ("child", "activity", "status", "requested_on", "approved_on")
    # Filters available in the right sidebar
    list_filter = ("status", "activity")
    # Fields available for the admin search bar
    search_fields = ("child__first_name", "child__last_name", "activity__title")
