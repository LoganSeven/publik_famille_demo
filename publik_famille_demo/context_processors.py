# publik_famille_demo/context_processors.py
"""
Custom context processors for the Publik Famille Demo project.

These processors inject project-specific variables into all
template contexts, making them available globally in templates.
"""

import os
from django.conf import settings


def branding(request):
    """
    Inject branding and backend configuration into the template context.

    Parameters
    ----------
    request : HttpRequest
        The current HTTP request.

    Returns
    -------
    dict
        A dictionary containing:
        - ``EO_LOGO_URL`` : URL for Entr'ouvert logo, or None if unset.
        - ``PUBLIK_LOGO_URL`` : URL for Publik logo, or None if unset.
        - ``BILLING_BACKEND`` : Configured billing backend (default: ``local``).
        - ``ENROLLMENT_BACKEND`` : Configured enrollment backend (default: ``local``).

    Notes
    -----
    These values are defined in Django settings and can be
    customized via environment variables.
    """
    return {
        "EO_LOGO_URL": getattr(settings, "EO_LOGO_URL", None),
        "PUBLIK_LOGO_URL": getattr(settings, "PUBLIK_LOGO_URL", None),
        "BILLING_BACKEND": getattr(settings, "BILLING_BACKEND", "local"),
        "ENROLLMENT_BACKEND": getattr(settings, "ENROLLMENT_BACKEND", "local"),
    }
