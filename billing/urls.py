# billing/urls.py
"""
URL configuration for the billing application.

This module defines routes for invoice-related actions,
such as paying an invoice.
"""

from django.urls import path
from .views import pay_invoice

# Application namespace for reverse lookups
app_name = "billing"

#: URL patterns for the billing application
urlpatterns = [
    # Endpoint for paying a specific invoice by primary key
    path("payer/<int:pk>/", pay_invoice, name="pay_invoice"),
]
