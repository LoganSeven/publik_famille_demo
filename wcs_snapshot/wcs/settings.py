# Django settings for wcs project.

import os

DEBUG = True

PROJECT_PATH = os.path.dirname(os.path.dirname(__file__))

ADMINS = (
    # ('Your Name', 'your_email@example.com'),
)

MANAGERS = ADMINS

# w.c.s. doesn't use Django ORM (yet) so do not declare any database for now.
DATABASES = {}

# Hosts/domain names that are valid for this site; required if DEBUG is False
# See https://docs.djangoproject.com/en/1.5/ref/settings/#allowed-hosts
ALLOWED_HOSTS = []

# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# In a Windows environment this must be set to your system time zone.
TIME_ZONE = 'Europe/Brussels'

# Language code for this installation. All choices can be found here:
# http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGE_CODE = 'en-us'

LANGUAGES = (('en', 'English'), ('fr', 'French'), ('de', 'German'))

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale.

# If you set this to False, Django will not use timezone-aware datetimes.
USE_TZ = True

LOCALE_PATHS = (os.path.join(PROJECT_PATH, 'wcs', 'locale'),)

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/var/www/example.com/media/"
MEDIA_ROOT = os.path.join(PROJECT_PATH, 'media')

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash.
# Examples: "http://example.com/media/", "http://media.example.com/"
MEDIA_URL = '/media/'

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/var/www/example.com/static/"
STATIC_ROOT = os.path.join(PROJECT_PATH, 'static')

# URL prefix for static files.
# Example: "http://example.com/static/", "http://static.example.com/"
STATIC_URL = '/static/'

# Additional locations of static files
STATICFILES_DIRS = ()

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = 'k16cal%1fnochq4xbxqgdns-21lt9lxeof5*%j(0ief3=db32&'

# Templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(PROJECT_PATH, 'wcs', 'templates'),
        ],
        'APP_DIRS': False,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                'wcs.context_processors.publisher',
            ],
            'loaders': [
                'wcs.utils.TemplateLoader',
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            'builtins': [
                'django.templatetags.l10n',
                'django.contrib.humanize.templatetags.humanize',
                'wcs.qommon.templatetags.qommon',
            ],
        },
    },
]

MIDDLEWARE = (
    'django.middleware.common.CommonMiddleware',
    'wcs.middleware.PublisherInitialisationMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'wcs.compat.PublishErrorMiddleware',
    'wcs.middleware.MaintenanceMiddleware',
    'wcs.middleware.AfterJobsMiddleware',
)

ROOT_URLCONF = 'wcs.urls'

# Python dotted path to the WSGI application used by Django's runserver.
WSGI_APPLICATION = 'wcs.wsgi.application'

# custom date formats
FORMAT_MODULE_PATH = 'wcs.formats'

INSTALLED_APPS = (
    'gadjo',
    'wcs.ctl',
    'wcs.qommon',
    'django.contrib.staticfiles',
)

CACHES = {
    'default': {
        'BACKEND': 'wcs.cache.WcsTenantCache',
        # add a real Django cache backend, with its parameters if needed
        'REAL_BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'wcs',
    }
}

CKEDITOR_CONFIGS = {
    'default': {
        'allowedContent': True,
        'removePlugins': 'stylesheetparser',
        'toolbar_Own': [
            ['Source', 'Format', '-', 'Bold', 'Italic'],
            ['NumberedList', 'BulletedList'],
            ['JustifyLeft', 'JustifyCenter', 'JustifyRight', 'JustifyBlock'],
            ['Link', 'Unlink'],
            ['Image', '-', 'HorizontalRule'],
            [
                'RemoveFormat',
            ],
            ['Maximize'],
        ],
        'toolbar': 'Own',
        'resize_enabled': False,
    },
}

WCS_LEGACY_CONFIG_FILE = None

# management command, used to run afterjobs in uwsgi mode,
# usually /usr/bin/wcs-manage.
WCS_MANAGE_COMMAND = None

# proxies=REQUESTS_PROXIES is used in python-requests call
# http://docs.python-requests.org/en/master/user/advanced/?highlight=proxy#proxies
REQUESTS_PROXIES = None

# timeout used in python-requests call, in seconds
# we use 28s by default: timeout just before web server, which is usually 30s
REQUESTS_TIMEOUT = 28

# REQUESTS_CERT is a dict of 'url_prefix': cert. cert is used in python-requests call
# https://docs.python-requests.org/en/master/user/advanced/#client-side-certificates
# example : REQUESTS_CERT = {'https://example.net/ssl-auth/': '/path/client.pem'}
REQUESTS_CERT = {}

# For high availability installations with multiple instances of w.c.s.
# components, one should disable cron jobs execution on secondary servers;
# set the following variable True disables "cron" management command
DISABLE_CRON_JOBS = False

# w.c.s. can have very large forms, in backoffice and frontoffice
DATA_UPLOAD_MAX_NUMBER_FIELDS = 3000  # Django default is 1000

# workalendar config
WORKING_DAY_CALENDAR = 'workalendar.europe.France'

# CRON_WORKERS
CRON_WORKERS = os.cpu_count() // 2 + 1

# how to run afterjobs
# accepted values are 'auto' (default mode, afterjobs are handled in thread or using
# the uwsgi spooler), 'tests' (force in-process mode), and 'thread' (force thread mode)
AFTERJOB_MODE = 'auto'

# SITE OPTIONS FLAGS DEFAULT VALUES
USE_LEGACY_QUERY_STRING_IN_LISTINGS = False
USE_STRICT_CHECK_FOR_VERIFICATION_FIELDS = False
DISABLED_VALIDATION_TYPES = ''  # string with validation types separated by commas (wildcard are allowed)

# default settings for geocoding service; nominatim.openstreetmap.org is set
# for convenience, you should check their terms of service.
NOMINATIM_URL = 'https://nominatim.openstreetmap.org'
NOMINATIM_KEY = None
NOMINATIM_CONTACT_EMAIL = None

# default setting for map tile service.
MAP_TILE_URLTEMPLATE = 'https://tiles.entrouvert.org/hdm/{z}/{x}/{y}.png'

local_settings_file = os.environ.get(
    'WCS_SETTINGS_FILE', os.path.join(os.path.dirname(__file__), 'local_settings.py')
)
if os.path.exists(local_settings_file):
    with open(local_settings_file) as fd:
        exec(fd.read())  # noqa pylint: disable=exec-used
