# Configuration for lingo.

# Override with /etc/lingo/settings.d/ files

# Lingo is a Django application: for the full list of settings and their
# values, see https://docs.djangoproject.com/en/3.2/ref/settings/
# For more information on settings see
# https://docs.djangoproject.com/en/3.2/topics/settings/

# WARNING! Quick-start development settings unsuitable for production!
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

# This file is sourced by "exec(open(...).read())" from
# /usr/lib/lingo/debian_config.py

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

# ALLOWED_HOSTS must be correct in production!
# See https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = [
    '*',
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Europe/Paris'
