# accounts/middleware.py
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin


def _should_gate(request) -> bool:
    if request.method != "POST":
        return False
    path = (request.path or "").rstrip("/")
    if not path.endswith("/inscrire"):
        return False
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return False
    try:
        return not bool(user.profile.id_verified)
    except Exception:
        return True


class EnforceIdentityVerificationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if _should_gate(request):
            next_url = request.get_full_path()
            url = reverse("accounts_verify_identity")
            messages.error(request, "Vérification d'identité requise.")
            return redirect(f"{url}?next={next_url}")
        return None
