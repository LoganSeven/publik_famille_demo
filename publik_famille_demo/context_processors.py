# publik_famille_demo/context_processors.py
import os
from django.conf import settings

def branding(request):
    return {
        'EO_LOGO_URL': getattr(settings, 'EO_LOGO_URL', None),
        'PUBLIK_LOGO_URL': getattr(settings, 'PUBLIK_LOGO_URL', None),
        'BILLING_BACKEND': getattr(settings, 'BILLING_BACKEND', 'local'),
        'ENROLLMENT_BACKEND': getattr(settings, 'ENROLLMENT_BACKEND', 'local'),
    }
