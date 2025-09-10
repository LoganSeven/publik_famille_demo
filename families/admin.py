# families/admin.py
from django.contrib import admin
from .models import Child
@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'parent', 'birth_date')
    list_filter = ('parent',)
    search_fields = ('first_name', 'last_name', 'parent__username')
