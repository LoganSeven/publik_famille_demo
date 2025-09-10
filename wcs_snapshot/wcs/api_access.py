# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import json
import urllib.parse

from quixote import get_publisher

from .qommon.errors import ConnectionError
from .qommon.misc import http_post_request
from .sql_criterias import Contains, Equal


class ApiAccess:
    _names = 'apiaccess'
    xml_root_node = 'apiaccess'
    name = None
    access_identifier = None
    access_key = None
    description = None
    restrict_to_anonymised_data = False
    idp_api_client = False
    _roles = None
    _role_ids = Ellipsis

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('description', 'str'),
        ('access_identifier', 'str'),
        ('access_key', 'str'),
        ('restrict_to_anonymised_data', 'bool'),
        ('roles', 'roles'),
        ('idp_api_client', 'bool'),
    ]

    @classmethod
    def get_by_identifier(cls, access_identifier):
        cached_value = get_publisher()._cached_objects['apiaccess_identifier'].get(access_identifier)
        if cached_value:
            return cached_value

        clauses = [Equal('access_identifier', access_identifier)]
        for api_access in cls.select(clause=clauses):
            if api_access.access_identifier == access_identifier:
                get_publisher()._cached_objects['apiaccess_identifier'][access_identifier] = api_access
                return api_access
        return None

    @classmethod
    def get_access_key(cls, access_identifier):
        api_access = cls.get_by_identifier(access_identifier)
        if api_access:
            return api_access.access_key
        return None

    def get_roles(self):
        return self.roles or []

    def get_role_ids(self):
        if self._role_ids is not Ellipsis:
            return self._role_ids
        self._role_ids = [x.id for x in self.get_roles() if x]
        return self._role_ids

    def get_as_api_user(self):
        class RestrictedApiUser:
            # kept as inner class so cannot be pickled
            id = Ellipsis  # make sure it fails all over the place if used
            is_admin = False
            is_api_user = True

            def __init__(self, api_access):
                self.api_access = api_access

            def can_go_in_admin(self):
                return False

            def can_go_in_backoffice(self):
                return False

            def get_roles(self):
                return self.roles

            def get_substitution_variables(self):
                return {}

        user = RestrictedApiUser(self)
        user.roles = self.get_role_ids()
        return user

    @classmethod
    def get_with_credentials(cls, username, password, client_ip=None):
        api_access = cls.get_by_identifier(username)
        if not api_access or api_access.access_key != password or api_access.idp_api_client:
            api_access = cls.get_from_idp(username, password, client_ip)
            if not api_access:
                raise KeyError
        return api_access.get_as_api_user()

    @property
    def roles(self):
        return self._roles() if callable(self._roles) else self._roles

    @roles.setter
    def roles(self, value):
        self._roles = value

    @classmethod
    def get_from_idp(cls, username, password, client_ip=None):
        from wcs.api_utils import get_secret_and_orig, sign_url

        idp_api_url = get_publisher().get_site_option('idp_api_url', 'variables') or ''
        if not idp_api_url:
            return None

        url = urllib.parse.urljoin(idp_api_url, 'check-api-client/')
        secret, orig = get_secret_and_orig(url)
        url += '?orig=%s' % orig
        headers = {
            'Accept': 'application/json',
            'Content-type': 'application/json',
        }
        try:
            response, status, _, _ = http_post_request(
                sign_url(url, secret),
                body=json.dumps({'identifier': username, 'password': password, 'ip': client_ip}),
                headers=headers,
            )
        except ConnectionError:
            return None

        if status != 200:
            return None

        data = response.json()
        if data.get('err', 1) != 0:
            return None

        # cache api client locally (without password or client ip), it is necessary
        # for serialization for afterjobs in uwsgi spooler.
        access_identifier = f'_idp_{username}'
        api_access = cls.get_by_identifier(access_identifier) or cls()
        api_access.idp_api_client = True
        api_access.access_identifier = access_identifier
        role_class = get_publisher().role_class
        try:
            api_access.restrict_to_anonymised_data = data['data']['restrict_to_anonymised_data']
            api_access.roles = role_class.select([Contains('uuid', data['data']['roles'])])
        except KeyError:
            return None
        api_access.store()
        return api_access
