# documents/admin.py
"""
Admin configuration for the documents application.

This module customizes the Django admin interface for the
Document model, providing list displays, filters, and
search capabilities.
"""

from django.contrib import admin
from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Document model.

    Defines how documents are displayed and managed within
    the Django admin interface.

    Attributes
    ----------
    list_display : tuple
        Fields displayed in the admin list view, including
        title, kind, user, and creation timestamp.
    list_filter : tuple
        Fields available as filters in the right sidebar,
        here the document kind.
    search_fields : tuple
        Fields searchable from the admin search bar,
        including document title, user username, and email.
    """

    # Columns displayed in the admin list view
    list_display = ("title", "kind", "user", "created_at")

    # Filters available in the right sidebar
    list_filter = ("kind",)

    # Fields searchable in the admin search bar
    search_fields = ("title", "user__username", "user__email")
