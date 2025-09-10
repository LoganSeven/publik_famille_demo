# lingo - payment and billing system
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

from django.utils.translation import gettext_lazy as _
from rest_framework import permissions
from rest_framework.response import Response as DRFResponse
from rest_framework.views import exception_handler as DRF_exception_handler

try:
    from hobo.rest_permissions import IsAPIClient

    has_hobo = True
except (ImportError, NameError):
    has_hobo = False


if has_hobo:
    APIAdmin = (~IsAPIClient) & permissions.IsAuthenticated
else:
    APIAdmin = permissions.IsAuthenticated


class Response(DRFResponse):
    def __init__(self, data=None, *args, **kwargs):
        if data and 'err' not in data:
            data['err'] = 0
        super().__init__(data, *args, **kwargs)


class APIError(Exception):
    http_status = 200

    def __init__(self, message, *args, err=1, err_class=None, errors=None):
        self.err_desc = _(message) % args
        self.err = err
        self.err_class = err_class or message % args
        self.errors = errors
        super().__init__(self.err_desc)

    def to_response(self):
        data = {
            'err': self.err,
            'err_class': self.err_class,
            'err_desc': self.err_desc,
        }
        if self.errors:
            data['errors'] = self.errors
        return Response(data, status=self.http_status)


class APIErrorBadRequest(APIError):
    http_status = 400


def exception_handler(exc, context):
    if isinstance(exc, APIError):
        return exc.to_response()

    return DRF_exception_handler(exc, context)
