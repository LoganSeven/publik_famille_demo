# publik_famille_demo/urls.py
"""
Root URL configuration for the Publik Famille Demo project.

This module defines the global URL routes and delegates
to application-specific ``urls.py`` modules. It also
serves media files in development mode.

For more details, see:
https://docs.djangoproject.com/en/stable/topics/http/urls/
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static

#: Global URL patterns for the project
urlpatterns = [
    # Django admin interface
    path("admin/", admin.site.urls),

    # Accounts application (authentication, identity verification, signup)
    path("accounts/", include("accounts.urls")),  # no namespace here

    # Families application (children and household management)
    path("families/", include("families.urls")),

    # Activities application (activities and enrollments)
    path("activities/", include("activities.urls")),

    # Billing application (invoices and payments)
    path("billing/", include("billing.urls")),

    # Documents application (PDFs, invoices, etc.)
    path("documents/", include("documents.urls")),

    # Monitoring application (system and application logs)
    path("monitoring/", include("monitoring.urls")),

    # Homepage
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
]

# Serve media files during development (not recommended in production)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
