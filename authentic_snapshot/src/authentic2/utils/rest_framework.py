# authentic2 - versatile identity manager
# Copyright (C) 2022 Entr'ouvert
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
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

_FLATTEN_SEPARATOR = '/'


def _is_number(string):
    if hasattr(string, 'isdecimal'):
        return string.isdecimal() and [ord(c) < 256 for c in string]
    else:  # str PY2
        return string.isdigit()


def _unflatten(d, separator=_FLATTEN_SEPARATOR):
    """Transform:

       {"a/b/0/x": "1234"}

    into:

       {"a": {"b": [{"x": "1234"}]}}
    """
    if not isinstance(d, dict) or not d:  # unflattening an empty dict has no sense
        return d

    # ok d is a dict

    def map_digits(parts):
        return [int(x) if _is_number(x) else x for x in parts]

    keys = [(map_digits(key.split(separator)), key) for key in d]
    keys.sort()

    def set_path(path, orig_key, d, value, i=0):
        assert path

        key, tail = path[i], path[i + 1 :]

        if not tail:  # end of path, set thevalue
            if isinstance(key, int):
                assert isinstance(d, list)
                if len(d) != key:
                    raise ValueError('incomplete array before %s' % orig_key)
                d.append(value)
            else:
                assert isinstance(d, dict)
                d[key] = value
        else:
            new = [] if isinstance(tail[0], int) else {}

            if isinstance(key, int):
                assert isinstance(d, list)
                if len(d) < key:
                    raise ValueError(
                        'incomplete array before %s in %s'
                        % (separator.join(map(str, path[: i + 1])), orig_key)
                    )
                if len(d) == key:
                    d.append(new)
                else:
                    new = d[key]
            else:
                new = d.setdefault(key, new)
            set_path(path, orig_key, new, value, i + 1)

    # Is the first level an array or a dict ?
    if isinstance(keys[0][0][0], int):
        new = []
    else:
        new = {}
    for path, key in keys:
        value = d[key]
        set_path(path, key, new, value)
    return new


class UnflattenJSONParser(JSONParser):
    def parse(self, *args, **kwargs):
        result = super().parse(*args, **kwargs)
        if isinstance(result, dict) and any('/' in key for key in result):
            result = _unflatten(result)
        return result


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
