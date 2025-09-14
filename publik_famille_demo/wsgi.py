# publik_famille_demo/wsgi.py
"""
WSGI config for the Publik Famille Demo project.

This module exposes the WSGI callable as a module-level variable
named ``application``. It is used by WSGI servers such as
Gunicorn, uWSGI, or Django’s built-in runserver to serve the project.

For more details, see:
https://docs.djangoproject.com/en/stable/howto/deployment/wsgi/
"""

import os
from django.core.wsgi import get_wsgi_application

# Set the default Django settings module if not already defined
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "publik_famille_demo.settings")

#: The WSGI application callable used by WSGI servers
application = get_wsgi_application()
