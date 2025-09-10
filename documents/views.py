# documents/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView
from .models import Document, DocumentKind

class DocumentListView(LoginRequiredMixin, ListView):
    template_name = 'documents/document_list.html'
    context_object_name = 'documents'
    def get_queryset(self):
        return Document.objects.filter(user=self.request.user)

class InvoiceListView(LoginRequiredMixin, ListView):
    template_name = 'documents/invoice_list.html'
    context_object_name = 'documents'
    def get_queryset(self):
        return Document.objects.filter(user=self.request.user, kind=DocumentKind.FACTURE)
