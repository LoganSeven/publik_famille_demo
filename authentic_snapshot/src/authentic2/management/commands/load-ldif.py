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

import argparse
import json
import logging

import ldif
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.transaction import atomic

from authentic2.hashers import olap_password_to_dj
from authentic2.models import Attribute


class DjangoUserLDIFParser(ldif.LDIFParser):
    def __init__(self, *args, **kwargs):
        self.options = kwargs.pop('options')
        self.command = kwargs.pop('command')
        self.users = []
        self.log = logging.getLogger(__name__)
        self.__dict__.update(self.options)
        self.json = []
        self.callback = None
        if 'callback' in self.options:
            d = globals().copy()
            with open(self.options['callback'], d) as fd:
                exec(fd.read())
            self.callback = d.get('callback')
        ldif.LDIFParser.__init__(self, *args, **kwargs)

    def handle(self, dn, entry):
        User = get_user_model()
        if self.object_class not in entry['objectClass']:
            if self.verbosity >= 2:
                self.command.stdout.write('Ignoring entry %r' % dn)
        u = User()
        a = []
        m = []
        d = {'dn': dn}
        for key in entry:
            v = entry[key][0]
            v = v.decode('utf-8')
            for attribute in ('first_name', 'last_name', 'username', 'email', 'password'):
                if key != self.options[attribute]:
                    continue
                if attribute == 'password':
                    v = olap_password_to_dj(v)
                elif attribute == 'username' and self.options['realm']:
                    v += '@%s' % self.options['realm']
                setattr(u, attribute, v)
                d[attribute] = v
            for attribute in self.options['extra_attribute']:
                if key != attribute:
                    continue
                attribute = self.options['extra_attribute'][attribute]
                a.append((attribute, v))
                d[attribute] = v
        if self.callback:
            m.extend(self.callback(u, dn, entry, self.options, d))
        if 'username' not in d:
            self.log.warning(
                'cannot load dn %s, username cannot be initialized from the field %s',
                dn,
                self.options['username'],
            )
            return
        try:
            old = User.objects.get(username=d['username'])
            u.id = old.id
        except User.DoesNotExist:
            pass
        self.log.debug('loaded user %r from ldif', d)
        self.json.append(d)
        self.users.append((u, a, m))

    def parse(self, *args, **kwargs):
        ldif.LDIFParser.parse(self, *args, **kwargs)
        if self.options['result']:
            with open(self.options['result'], 'w') as f:
                json.dump(self.json, f)


class ExtraAttributeAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        ldap_attribute, django_attribute = values
        try:
            Attribute.objects.get(name=django_attribute)
        except Attribute.DoesNotExist:
            raise argparse.ArgumentTypeError('django attribute %s does not exist' % django_attribute)
        res = getattr(namespace, self.dest, {})
        res[ldap_attribute] = django_attribute
        setattr(namespace, self.dest, res)


class Command(BaseCommand):
    '''Load LDAP ldif file'''

    can_import_django_settings = True
    requires_system_checks = '__all__'
    help = 'Load/update LDIF files as users'

    def add_arguments(self, parser):
        parser.add_argument('ldif_file', nargs='+')
        parser.add_argument('--first-name', default='givenName', help='attribute used to set the first name')
        parser.add_argument('--last-name', default='sn', help='attribute used to set the last name')
        parser.add_argument('--email', default='mail', help='attribute used to set the email')
        parser.add_argument('--username', default='uid', help='attribute used to set the username')
        parser.add_argument(
            '--password',
            default='userPassword',
            help='attribute to extract the password from, OpenLDAP hashing algorithm are recognized',
        )
        parser.add_argument('--object-class', default='inetOrgPerson', help='object class of records to load')
        parser.add_argument(
            '--extra-attribute',
            default={},
            action=ExtraAttributeAction,
            nargs=2,
            help='object class of records to load',
        )
        parser.add_argument('--result', default=None, help='file to store a JSON log of created users')
        parser.add_argument('--fake', action='store_true', help='file to store a JSON log of created users')
        parser.add_argument('--realm', default=None, help='realm for the new users')
        parser.add_argument(
            '--callback',
            default=None,
            help=(
                'python file containing a function callback(user, dn, entry, options, dump) it can return'
                ' models that will be saved'
            ),
        )
        parser.add_argument('--callback-arg', action='append', help='arguments for the callback')

    @atomic
    def handle(self, *args, **options):
        options['verbosity'] = int(options['verbosity'])
        ldif_files = options.pop('ldif_file')
        for arg in ldif_files:
            with open(arg) as fd:
                parser = DjangoUserLDIFParser(fd, options=options, command=self)
                parser.parse()
                if not options['fake']:
                    for u, a, m in parser.users:
                        u.save()
                        for attribute, value in a:
                            setattr(u.attributes, attribute, value)
                        for model, kwargs in m:
                            model.objects.get_or_create(**kwargs)
