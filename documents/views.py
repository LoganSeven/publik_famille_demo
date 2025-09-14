# documents/views.py
"""
Views for the documents application.

This module defines class-based views for listing documents,
including all documents of a user and invoices specifically.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView
from .models import Document, DocumentKind


class DocumentListView(LoginRequiredMixin, ListView):
    """
    View for listing all documents of the authenticated user.

    Requires login and restricts results to the current user's documents.
    """

    template_name = "documents/document_list.html"
    context_object_name = "documents"

    def get_queryset(self):
        """
        Return the queryset of documents for the current user.

        Returns
        -------
        QuerySet
            All Document instances belonging to the authenticated user.
        """
        return Document.objects.filter(user=self.request.user)


class InvoiceListView(LoginRequiredMixin, ListView):
    """
    View for listing only invoice documents of the authenticated user.

    Requires login and restricts results to documents of type FACTURE
    belonging to the current user.
    """

    template_name = "documents/invoice_list.html"
    context_object_name = "documents"

    def get_queryset(self):
        """
        Return the queryset of invoice documents for the current user.

        Returns
        -------
        QuerySet
            All Document instances with kind FACTURE
            belonging to the authenticated user.
        """
        return Document.objects.filter(
            user=self.request.user, kind=DocumentKind.FACTURE
        )
