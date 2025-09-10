# This file is sourced by "exec(open(..." from wcs.settings

import os

PROJECT_NAME = 'wcs'
WCS_MANAGE_COMMAND = '/usr/bin/wcs-manage'

#
# hobotization
#
exec(open('/usr/lib/hobo/debian_config_common.py').read())

# and some hobo parts that are specific to w.c.s.
TEMPLATES[0]['OPTIONS']['context_processors'] = [
    'hobo.context_processors.template_vars',
    'hobo.context_processors.theme_base',
    'hobo.context_processors.user_urls',
] + TEMPLATES[0]['OPTIONS']['context_processors']

MIDDLEWARE = (
    'hobo.middleware.utils.StoreRequestMiddleware',
    'hobo.middleware.xforwardedfor.XForwardedForMiddleware',
    'hobo.middleware.VersionMiddleware',  # /__version__
    'hobo.middleware.cors.CORSMiddleware',
) + MIDDLEWARE

CACHES = {
    'default': {
        'BACKEND': 'wcs.cache.WcsTenantCache',
        # add a real Django cache backend, with its parameters if needed
        'REAL_BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'LOCATION': '127.0.0.1:11211',
    }
}

# don't rely on hobo logging as it requires hobo multitenant support.
LOGGING = {}
LOGGING_CONFIG = None

#
# local settings
#
exec(open(os.path.join(ETC_DIR, 'settings.py')).read())

# run additional settings snippets
exec(open('/usr/lib/hobo/debian_config_settings_d.py').read())
