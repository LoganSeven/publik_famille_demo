# publik_famille_demo/wsgi.py
import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'publik_famille_demo.settings')
application = get_wsgi_application()
