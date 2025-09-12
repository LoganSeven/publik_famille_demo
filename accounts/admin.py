# accounts/admin.py
from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "id_verified")
    list_filter = ("id_verified",)
    search_fields = ("user__username", "user__email")
