# accounts/apps.py
from importlib import import_module

from django.apps import AppConfig
from django.conf import settings
from django.urls import path


class AccountsConfig(AppConfig):
    name = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        self._ensure_middleware()
        self._ensure_verify_urls()

    def _ensure_middleware(self) -> None:
        target = "accounts.middleware.EnforceIdentityVerificationMiddleware"
        current = list(getattr(settings, "MIDDLEWARE", ()))
        if target not in current:
            try:
                i = current.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1
            except ValueError:
                i = 0
            current.insert(i, target)
            settings.MIDDLEWARE = tuple(current)

    def _ensure_verify_urls(self) -> None:
        root = settings.ROOT_URLCONF
        mod = import_module(root)
        up = getattr(mod, "urlpatterns", None)
        if not isinstance(up, list):
            return
        names = {getattr(p, "name", "") for p in up}
        if "accounts_verify_identity" in names and "accounts_verify_start" in names and "accounts_verify_callback" in names:
            return
        from accounts.views_identity import verify_identity, verify_start, verify_callback

        patterns = [
            path("accounts/verify/", verify_identity, name="accounts_verify_identity"),
            path("accounts/verify/start/", verify_start, name="accounts_verify_start"),
            path("accounts/verify/callback/", verify_callback, name="accounts_verify_callback"),
        ]
        up.extend(patterns)
