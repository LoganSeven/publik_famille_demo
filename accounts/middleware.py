# accounts/middleware.py
"""
Middleware for enforcing identity verification.

This module defines middleware that ensures users must verify
their identity before accessing enrollment-related views.
Admins and superusers are exempt from this restriction.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


def _is_admin(user) -> bool:
    """
    Check whether a user is an administrator.

    Parameters
    ----------
    user : User
        The user instance to evaluate.

    Returns
    -------
    bool
        True if the user is authenticated and has staff
        or superuser privileges, False otherwise.
    """
    return user.is_authenticated and (user.is_staff or user.is_superuser)


class IdentityVerificationMiddleware:
    """
    Middleware enforcing identity verification for protected views.

    If the request targets a view listed in the setting
    ``IDENTITY_ENROLL_URL_NAMES`` and the user is not verified,
    the middleware redirects to the identity verification process.
    """

    def __init__(self, get_response):
        """
        Initialize the middleware.

        Parameters
        ----------
        get_response : callable
            The next middleware or view in the chain.
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        Process incoming requests and enforce identity verification.

        Parameters
        ----------
        request : HttpRequest
            The incoming HTTP request.

        Returns
        -------
        HttpResponse
            Either a redirection to the identity verification
            page or the standard response from the next handler.
        """
        match = getattr(request, "resolver_match", None)
        protected = getattr(settings, "IDENTITY_ENROLL_URL_NAMES", [])

        # Check if the current view requires identity verification
        if match and match.view_name in protected:
            user = request.user
            if user.is_authenticated and not _is_admin(user):
                profile = getattr(user, "profile", None)
                if not profile or not profile.id_verified:
                    return redirect(
                        f"{reverse('accounts_verify_identity')}?next={request.get_full_path()}"
                    )

        return self.get_response(request)
