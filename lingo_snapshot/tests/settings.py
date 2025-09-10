import os

TIME_ZONE = 'Europe/Paris'
LANGUAGE_CODE = 'en-us'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.BasicAuthentication'],
    'EXCEPTION_HANDLER': 'lingo.api.utils.exception_handler',
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    },
    'dummy': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'TEST': {
            'NAME': ('lingo-test-%s' % os.environ.get('BRANCH_NAME', '').replace('/', '-'))[:63],
        },
    }
}

KNOWN_SERVICES = {
    'chrono': {
        'default': {
            'title': 'test',
            'url': 'http://chrono.example.org/',
            'secret': 'lingo',
            'orig': 'lingo',
            'backoffice-menu-url': 'http://chrono.example.org/manage/',
            'secondary': False,
        },
        'other': {
            'title': 'other',
            'url': 'http://other.chrono.example.org/',
            'secret': 'lingo',
            'orig': 'lingo',
            'backoffice-menu-url': 'http://other.chrono.example.org/manage/',
            'secondary': True,
        },
    },
    'wcs': {
        'default': {
            'title': 'test',
            'url': 'http://wcs.example.org/',
            'secret': 'lingo',
            'orig': 'lingo',
            'backoffice-menu-url': 'http://wcs.example.org/manage/',
        },
    },
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'lingo.api.utils.exception_handler',
    # this is the default value but by explicitely setting it
    # we avoid a collision with django-webtest erasing the setting
    # while patching it
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
}
