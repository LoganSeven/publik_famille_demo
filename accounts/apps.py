# accounts/apps.py
"""
Application configuration for the accounts module.

This module defines the AccountsConfig class, which ensures that
middleware and identity verification URLs are automatically
injected into the Django project configuration at startup.
"""

from importlib import import_module
from django.apps import AppConfig
from django.conf import settings
from django.urls import path


class AccountsConfig(AppConfig):
    """
    Configuration class for the accounts application.

    Attributes
    ----------
    default_auto_field : str
        The default type for auto-created primary key fields.
    name : str
        The full Python path to the application.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self) -> None:
        """
        Initialize the accounts application.

        Ensures that:
        - The identity verification middleware is injected into the project.
        - Identity verification URLs are added if missing.
        - Signals are imported to register event handlers.
        """
        self._ensure_middleware()
        self._ensure_verify_urls()
        from . import signals  # noqa: F401  # Ensure signal registration

    def _ensure_middleware(self) -> None:
        """
        Ensure that the IdentityVerificationMiddleware is loaded.

        Inserts the middleware immediately after Django's
        AuthenticationMiddleware if not already present.
        """
        target = "accounts.middleware.IdentityVerificationMiddleware"
        current = list(getattr(settings, "MIDDLEWARE", ()))
        if target not in current:
            try:
                # Insert after AuthenticationMiddleware
                i = current.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1
            except ValueError:
                # If not found, insert at the beginning
                i = 0
            current.insert(i, target)
            settings.MIDDLEWARE = tuple(current)

    def _ensure_verify_urls(self) -> None:
        """
        Ensure that identity verification URLs are registered.

        If the main urlpatterns list does not already include the
        identity verification routes, they are added dynamically.
        """
        root = settings.ROOT_URLCONF
        mod = import_module(root)
        up = getattr(mod, "urlpatterns", None)
        if not isinstance(up, list):
            return

        names = {getattr(p, "name", "") for p in up}
        if {"accounts_verify_identity", "accounts_verify_start", "accounts_verify_callback"} <= names:
            return

        from accounts.views_identity import verify_identity, verify_start, verify_callback

        patterns = [
            path("accounts/verify/", verify_identity, name="accounts_verify_identity"),
            path("accounts/verify/start/", verify_start, name="accounts_verify_start"),
            path("accounts/verify/callback/", verify_callback, name="accounts_verify_callback"),
        ]
        up.extend(patterns)
