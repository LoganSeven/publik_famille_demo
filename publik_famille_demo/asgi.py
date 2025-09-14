# publik_famille_demo/asgi.py
"""
ASGI config for the Publik Famille Demo project.

This module exposes the ASGI callable as a module-level variable
named ``application``. It is used by ASGI servers such as
Daphne, Uvicorn, or Hypercorn to serve the project.

For more details, see:
https://docs.djangoproject.com/en/stable/howto/deployment/asgi/
"""

import os
from django.core.asgi import get_asgi_application

# Set the default Django settings module if not already defined
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "publik_famille_demo.settings")

#: The ASGI application callable used by ASGI servers
application = get_asgi_application()
