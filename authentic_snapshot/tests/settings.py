# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
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

import os

# use a faster hasing scheme for passwords
if 'PASSWORD_HASHERS' not in locals():
    from django.conf.global_settings import PASSWORD_HASHERS
else:
    PASSWORD_HASHERS = locals()['PASSWORD_HASHERS']

PASSWORD_HASHERS = ['django.contrib.auth.hashers.UnsaltedMD5PasswordHasher'] + list(PASSWORD_HASHERS)

A2_CACHE_ENABLED = False

LANGUAGE_CODE = 'en'

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.postgresql_psycopg2'),
        'NAME': 'authentic2',
    }
}

if 'postgres' in DATABASES['default']['ENGINE']:
    for key in ('PGPORT', 'PGHOST', 'PGUSER', 'PGPASSWORD'):
        if key in os.environ:
            DATABASES['default'][key[2:]] = os.environ[key]


ALLOWED_HOSTS = ALLOWED_HOSTS + [  # pylint: disable=used-before-assignment
    'testserver',
    'example.net',
    'cache1.example.com',
    'cache2.example.com',
]

KNOWN_SERVICES = {
    'wcs': {
        'eservices': {
            'title': 'test',
            'url': 'http://example.org',
            'secret': 'chrono',
            'orig': 'chrono',
            'backoffice-menu-url': 'http://example.org/manage/',
        }
    },
    'passerelle': {
        'passerelle': {
            'title': 'test',
            'url': 'https://foo.whatever.none',
            'secret': 'passerelle',
            'orig': 'passerelle',
            'backoffice-menu-url': 'http://foo.whatever.none/manage/',
        }
    },
}

A2_AUTH_KERBEROS_ENABLED = False

A2_VALIDATE_EMAIL_DOMAIN = False

A2_HOOKS_PROPAGATE_EXCEPTIONS = True

TEMPLATES[0]['DIRS'].insert(0, 'tests/templates')  # pylint: disable=undefined-variable
TEMPLATES[0]['OPTIONS']['debug'] = True  # pylint: disable=undefined-variable

SITE_BASE_URL = 'https://testserver'

A2_MAX_EMAILS_PER_IP = None
A2_MAX_EMAILS_FOR_ADDRESS = None

A2_TOKEN_EXISTS_WARNING = False
A2_REDIRECT_WHITELIST = ['http://sp.org/']

TESTING = True
