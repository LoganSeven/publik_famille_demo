# billing/urls.py
from django.urls import path
from .views import pay_invoice
app_name = 'billing'
urlpatterns = [ path('payer/<int:pk>/', pay_invoice, name='pay_invoice'), ]
