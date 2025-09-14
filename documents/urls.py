# documents/urls.py
"""
URL configuration for the documents application.

This module defines routes for listing documents,
including all documents and invoices specifically.
"""

from django.urls import path
from .views import DocumentListView, InvoiceListView

# Application namespace for reverse lookups
app_name = "documents"

#: URL patterns for the documents application
urlpatterns = [
    # List all documents for the authenticated user
    path("", DocumentListView.as_view(), name="list"),

    # List only invoice documents (kind=FACTURE) for the authenticated user
    path("factures/", InvoiceListView.as_view(), name="invoices"),
]
