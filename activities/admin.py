# activities/admin.py
from django.contrib import admin
from .models import Activity, Enrollment

@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ('title', 'fee', 'start_date', 'end_date', 'capacity', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('title',)

@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('child', 'activity', 'status', 'requested_on', 'approved_on')
    list_filter = ('status', 'activity')
    search_fields = ('child__first_name', 'child__last_name', 'activity__title')
