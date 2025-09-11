# Authentic2 © Entr'ouvert

import uuid

from django.urls.converters import SlugConverter, register_converter

from authentic2.utils.crypto import base64url_decode


class ObjectUUID:
    regex = r'[0-9a-f]{32}'

    def to_python(self, value):
        return value.replace('-', '')

    def to_url(self, value):
        return str(value)


class Base64UUID:
    regex = r'[A-Za-z0-9-_]{22}'

    def to_python(self, value):
        uuid_bytes = base64url_decode(value.encode('ascii'))
        return uuid.UUID(bytes=uuid_bytes)

    def to_url(self, value):
        return value


class UserImportUUID:
    regex = r'[A-Za-z0-9-_]{10,}'

    def to_python(self, value):
        return str(value)

    def to_url(self, value):
        return str(value)


class A2Token:
    regex = r'[1-Za-z0-9_ -]+'

    def to_python(self, value):
        return str(value)

    def to_url(self, value):
        return str(value)


def register_converters():
    register_converter(ObjectUUID, 'a2_uuid')
    register_converter(Base64UUID, 'a2_b64uuid')
    register_converter(A2Token, 'a2_token')
    # legacy, some user imports have non UUID like string as UserImport.uuid
    register_converter(UserImportUUID, 'a2_userimport_id')
    # legacy, some instances have non UUID like string as User.uuid
    register_converter(SlugConverter, 'user_uuid')


def string_to_boolean(value, default=None):
    if not isinstance(value, str):
        return default
    if value.lower() in ['t', 'true', 'yes', 'y', '1']:
        return True
    if value.lower() in ['f', 'false', 'no', 'n', '0']:
        return False
    return default
