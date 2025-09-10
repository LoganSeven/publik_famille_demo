# documents/admin.py
from django.contrib import admin
from .models import Document
@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'kind', 'user', 'created_at')
    list_filter = ('kind',)
    search_fields = ('title', 'user__username', 'user__email')
