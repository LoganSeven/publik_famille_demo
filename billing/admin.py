# billing/admin.py
from django.contrib import admin
from .models import Invoice
@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'enrollment', 'amount', 'status', 'issued_on', 'paid_on')
    list_filter = ('status',)
    search_fields = ('enrollment__child__first_name', 'enrollment__child__last_name', 'enrollment__activity__title')
