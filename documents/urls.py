# documents/urls.py
from django.urls import path
from .views import DocumentListView, InvoiceListView
app_name = 'documents'
urlpatterns = [
    path('', DocumentListView.as_view(), name='list'),
    path('factures/', InvoiceListView.as_view(), name='invoices'),
]
