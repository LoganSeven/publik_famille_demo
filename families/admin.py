# families/admin.py
"""
Admin configuration for the families application.

This module customizes the Django admin interface for the
Child model, providing list displays, filters, and search
capabilities.
"""

from django.contrib import admin
from .models import Child


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Child model.

    Defines how children are displayed and managed within
    the Django admin interface.

    Attributes
    ----------
    list_display : tuple
        Fields displayed in the admin list view, including
        first name, last name, parent, and birth date.
    list_filter : tuple
        Fields available as filters in the right sidebar,
        here the parent user.
    search_fields : tuple
        Fields searchable from the admin search bar,
        including child's first name, last name, and
        parent username.
    """

    # Columns displayed in the admin list view
    list_display = ("first_name", "last_name", "parent", "birth_date")

    # Filters available in the right sidebar
    list_filter = ("parent",)

    # Fields searchable in the admin search bar
    search_fields = ("first_name", "last_name", "parent__username")
