# accounts/middleware.py
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

def _is_admin(user) -> bool:
    return user.is_authenticated and (user.is_staff or user.is_superuser)

class IdentityVerificationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        match = getattr(request, 'resolver_match', None)
        if match and match.view_name in getattr(settings, 'IDENTITY_ENROLL_URL_NAMES', []):
            user = request.user
            if user.is_authenticated and not _is_admin(user):
                profile = getattr(user, 'profile', None)
                if not profile or not profile.id_verified:
                    return redirect(f"{reverse('accounts_verify_identity')}?next={request.get_full_path()}")
        return self.get_response(request)
