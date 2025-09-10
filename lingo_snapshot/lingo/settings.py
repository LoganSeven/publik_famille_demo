# lingo - payment and bill system
# Copyright (C) 2022  Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Django settings file; it loads the default settings, and local settings
(from a local_settings.py file, or a configuration file set in the
LINGO_SETTINGS_FILE environment variable).

The local settings file should exist, at least to set a suitable SECRET_KEY,
and to disable DEBUG mode in production.
"""

import os

from django.conf import global_settings

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'r^(w+o4*txe1=t+0w*w3*9%idij!yeq1#axpsi4%5*u#3u&)1t'  # nosec

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'ckeditor',
    'eopayment',
    'gadjo',
    'rest_framework',
    'django_filters',
    'sorl.thumbnail',
    'lingo.agendas',
    'lingo.api',
    'lingo.basket',
    'lingo.epayment',
    'lingo.export_import',
    'lingo.invoicing',
    'lingo.manager',
    'lingo.pricing',
    'lingo.snapshot',
    'lingo.callback',
)

MIDDLEWARE = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
)

# Serve xstatic files, required for gadjo
STATICFILES_FINDERS = list(global_settings.STATICFILES_FINDERS) + ['gadjo.finders.XStaticFinder']

# Templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'lingo', 'templates'),
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.request',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                'publik_django_templatetags.wcs.context_processors.wcs_objects',
            ],
            'builtins': [
                'publik_django_templatetags.publik.templatetags.publik',
                'publik_django_templatetags.wcs.templatetags.wcs',
                'django.contrib.humanize.templatetags.humanize',
            ],
        },
    },
]

ROOT_URLCONF = 'lingo.urls'

WSGI_APPLICATION = 'lingo.wsgi.application'


# Database

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# Internationalization

LANGUAGE_CODE = 'fr-fr'

TIME_ZONE = 'UTC'

USE_I18N = True


USE_TZ = True

LOCALE_PATHS = (os.path.join(BASE_DIR, 'lingo', 'locale'),)

# Static files (CSS, JavaScript, Images)

STATIC_URL = '/static/'

MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
MEDIA_URL = '/media/'

# mode for newly updated files
FILE_UPLOAD_PERMISSIONS = 0o644

# extra variables for templates
TEMPLATE_VARS = {}

# Authentication settings
try:
    import mellon
except ImportError:
    mellon = None

if mellon is not None:
    INSTALLED_APPS += ('mellon',)
    AUTHENTICATION_BACKENDS = (
        'mellon.backends.SAMLBackend',
        'django.contrib.auth.backends.ModelBackend',
    )

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_URL = '/logout/'

MELLON_ATTRIBUTE_MAPPING = {
    'email': '{attributes[email][0]}',
    'first_name': '{attributes[first_name][0]}',
    'last_name': '{attributes[last_name][0]}',
}

MELLON_SUPERUSER_MAPPING = {
    'is_superuser': 'true',
}

MELLON_USERNAME_TEMPLATE = '{attributes[name_id_content]}'

MELLON_IDENTITY_PROVIDERS = []

# proxies argument passed to all python-request methods
# (see http://docs.python-requests.org/en/master/user/advanced/#proxies)
REQUESTS_PROXIES = None

# timeout used in python-requests call, in seconds
# we use 28s by default: timeout just before web server, which is usually 30s
REQUESTS_TIMEOUT = 28

# default site
SITE_BASE_URL = 'http://localhost'

# known services
KNOWN_SERVICES = {}


def debug_show_toolbar(request):
    from debug_toolbar.middleware import show_toolbar as dt_show_toolbar  # pylint: disable=import-error

    return dt_show_toolbar(request) and not request.path.startswith('/__skeleton__/')


DEBUG_TOOLBAR_CONFIG = {'SHOW_TOOLBAR_CALLBACK': debug_show_toolbar}

REST_FRAMEWORK = {'EXCEPTION_HANDLER': 'lingo.api.utils.exception_handler'}

CKEDITOR_UPLOAD_PATH = 'uploads/'
CKEDITOR_IMAGE_BACKEND = 'pillow'

CKEDITOR_CONFIGS = {
    'default': {
        'allowedContent': False,
        'extraPlugins': 'stylescombo',
        'removePlugins': 'stylesheetparser',
        'toolbar_Own': [
            ['Styles'],
            ['Bold', 'Italic'],
            ['NumberedList', 'BulletedList'],
            ['JustifyLeft', 'JustifyCenter', 'JustifyRight', 'JustifyBlock'],
            ['Link', 'Unlink'],
            ['HorizontalRule'],
        ],
        'toolbar': 'Own',
        'resize_enabled': False,
        'width': '100%',
        'height': 150,
    },
}

BASKET_EXPIRY_DELAY = 60  # 1 hour by default

# max retries and timeout for HTTP requests during campaigns
CAMPAIGN_REQUEST_MAX_RETRIES = 3
CAMPAIGN_REQUEST_TIMEOUT = 10

# campaign options
SHOW_NON_INVOICED_LINES = False
CAMPAIGN_SHOW_FIX_ERROR = False
CAMPAIGN_ALLOW_PROMOTION_WITH_ERRORS = False

# campaign job options
CAMPAIGN_MAX_RUNNING_JOBS = 3
POOL_MAX_RUNNING_JOBS = 9
POOL_JOBS_PER_CAMPAIGN = 3

# max retries for callbacks
CALLBACK_MAX_RETRIES = 42

# from solr.thumbnail -- https://sorl-thumbnail.readthedocs.io/en/latest/reference/settings.html
THUMBNAIL_PRESERVE_FORMAT = True
THUMBNAIL_FORCE_OVERWRITE = False

local_settings_file = os.environ.get(
    'LINGO_SETTINGS_FILE', os.path.join(os.path.dirname(__file__), 'local_settings.py')
)
if os.path.exists(local_settings_file):
    with open(local_settings_file) as fd:
        exec(fd.read())  # nosec
