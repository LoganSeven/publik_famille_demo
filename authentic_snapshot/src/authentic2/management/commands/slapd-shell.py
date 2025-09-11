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


import io
import logging
import re
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from ldap.dn import escape_dn_chars
from ldif import LDIFWriter

COMMAND = 1
ATTR = 2

MAPPING = {
    'uuid': 'uid',
    'username': 'cn',
    'first_name': 'givenName',
    'last_name': 'sn',
    'email': 'mail',
}


def unescape_filter_chars(s):
    return re.sub(r'\\..', lambda s: s.group()[1:].decode('hex'), s)


class Command(BaseCommand):
    help = 'OpenLDAP shell backend'

    def ldap(self, command, attrs):
        self.logger.debug('received command %s %s', command, attrs)
        if command == 'SEARCH':
            out = io.BytesIO()
            ldif_writer = LDIFWriter(out)
            qs = get_user_model().objects.all()
            if attrs['filter'] != '(objectClass=*)':
                m = re.match(r'\((\w*)=(.*)\)', attrs['filter'])
                if not m:
                    print('RESULT')
                    print('code: 1')
                    print('info: invalid filter')
                    print()
                    return
                for user_attribute, ldap_attribute in MAPPING.items():
                    if ldap_attribute == m.group(1):
                        break
                else:
                    print('RESULT')
                    print('code: 1')
                    print('info: unknown attribute in filter')
                    print()
                    return
                value = m.group(2)
                if value.endswith('*') and value.startswith('*'):
                    user_attribute += '__icontains'
                    value = value[1:-1]
                elif value.endswith('*'):
                    user_attribute += '__istartswith'
                    value = value[:-1]
                elif value.startswith('*'):
                    user_attribute += '__iendswith'
                    value = value[1:]
                else:
                    user_attribute += '__iexact'
                value = unescape_filter_chars(value)
                qs = qs.filter(**{user_attribute: value.decode('utf-8')})
            for user in qs:
                o = {}
                for user_attribute, ldap_attribute in MAPPING.items():
                    o[ldap_attribute] = [str(getattr(user, user_attribute)).encode('utf-8')]
                o['objectClass'] = ['inetOrgPerson']
                dn = 'uid=%s,%s' % (escape_dn_chars(o['uid'][0]), attrs['suffix'])
                self.logger.debug('sending entry %s %s', dn, o)
                ldif_writer.unparse(dn, o)
            print(out.getvalue())
            out.close()
        print('RESULT')
        print('code: 0')
        print('info: RockNRoll')
        print()

    def handle(self, *args, **options):
        self.logger = logging.getLogger(__name__)
        state = COMMAND
        attrs = {}
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            if state == COMMAND:
                command = line.strip()
                state = ATTR
            elif state == ATTR:
                if line == '\n':
                    self.ldap(command, attrs)
                    state = COMMAND
                    attrs = {}
                    sys.stdout.flush()
                    sys.exit(0)
                else:
                    key, value = line.strip().split(':')
                    attrs[key] = value[1:]
