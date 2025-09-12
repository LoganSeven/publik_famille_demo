# billing/admin.py
"""
Admin configuration for the billing application.

This module customizes the Django admin interface for the
Invoice model, providing list displays, filters, and
search capabilities.
"""

from django.contrib import admin
from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Invoice model.

    Defines how invoices are displayed and managed within
    the Django admin interface.

    Attributes
    ----------
    list_display : tuple
        Fields displayed in the admin list view, including ID,
        related enrollment, amount, status, issued date, and
        paid date.
    list_filter : tuple
        Fields usable as filters in the right sidebar,
        here the invoice status.
    search_fields : tuple
        Fields searchable from the admin search bar, including
        child's first name, last name, and activity title.
    """

    # Columns displayed in the admin list view
    list_display = (
        "id",
        "enrollment",
        "amount",
        "status",
        "issued_on",
        "paid_on",
    )

    # Filters available in the right sidebar
    list_filter = ("status",)

    # Fields searchable in the admin search bar
    search_fields = (
        "enrollment__child__first_name",
        "enrollment__child__last_name",
        "enrollment__activity__title",
    )
