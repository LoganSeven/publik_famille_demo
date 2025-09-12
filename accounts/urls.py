# accounts/urls.py
"""
URL configuration for the accounts application.

This module defines URL patterns for user account management,
including sign-up, logout, identity verification, and built-in
Django authentication views.
"""

from django.urls import path, include
from .views import signup, logout_view
from . import views_identity

#: URL patterns for the accounts application
urlpatterns = [
    # User registration endpoint
    path("signup/", signup, name="signup"),

    # Custom logout endpoint
    path("logout/", logout_view, name="logout"),

    # Identity verification endpoints (simulation or external backend)
    path("verify/", views_identity.verify_identity, name="accounts_verify_identity"),
    path("verify/start/", views_identity.verify_start, name="accounts_verify_start"),
    path("verify/callback/", views_identity.verify_callback, name="accounts_verify_callback"),

    # Include Django's built-in authentication URLs (login, password reset, etc.)
    path("", include("django.contrib.auth.urls")),
]
