# publik_famille_demo/settings.py
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'demo-secret-key-change-me')
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Apps du projet
    'accounts.apps.AccountsConfig',
    'families.apps.FamiliesConfig',
    'activities.apps.ActivitiesConfig',
    'billing.apps.BillingConfig',
    'documents.apps.DocumentsConfig',
    'monitoring.apps.MonitoringConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Le middleware d’identité est inséré par AccountsConfig.ready()
]

ROOT_URLCONF = 'publik_famille_demo.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'publik_famille_demo' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'publik_famille_demo.context_processors.branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'publik_famille_demo.wsgi.application'
ASGI_APPLICATION = 'publik_famille_demo.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Europe/Paris'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'publik_famille_demo' / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1:8000', 'http://localhost:8000']
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

WCS_BASE_URL = os.getenv("WCS_BASE_URL")
WCS_API_TOKEN = os.getenv("WCS_API_TOKEN")
EO_LOGO_URL = os.getenv("EO_LOGO_URL")
PUBLIK_LOGO_URL = os.getenv("PUBLIK_LOGO_URL")

# Configuration d’identité
IDENTITY_BACKEND = os.environ.get('IDENTITY_BACKEND', 'simulation').lower()
IDENTITY_ENROLL_URL_NAMES = os.environ.get(
    'IDENTITY_ENROLL_URL_NAMES',
    'activities:enroll'
).split(',')

AUTHENTIC_AUTHORIZE_URL = os.environ.get('AUTHENTIC_AUTHORIZE_URL', '')
AUTHENTIC_TOKEN_URL = os.environ.get('AUTHENTIC_TOKEN_URL', '')
AUTHENTIC_USERINFO_URL = os.environ.get('AUTHENTIC_USERINFO_URL', '')
AUTHENTIC_CLIENT_ID = os.environ.get('AUTHENTIC_CLIENT_ID', '')
AUTHENTIC_CLIENT_SECRET = os.environ.get('AUTHENTIC_CLIENT_SECRET', '')
AUTHENTIC_REDIRECT_URI = os.environ.get('AUTHENTIC_REDIRECT_URI', '')
AUTHENTIC_DRY_RUN = os.environ.get('AUTHENTIC_DRY_RUN', '0') in {'1', 'true', 'True'}
