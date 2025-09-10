# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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

from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, Category
from wcs.formdef import FormDef

from . import TenantCommand


class Command(TenantCommand):
    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--all',
            action='store_true',
            help='wipe all form and card data',
        )
        parser.add_argument('--no-simulate', action='store_true', help='perform the wipe for real')

        parser.add_argument(
            '--form-categories',
            metavar='CATEGORIES',
            help='list of form categories (slugs, separated by commas)',
        )
        parser.add_argument('--forms', metavar='FORMS', help='list of forms (slugs, separated by commas)')
        parser.add_argument(
            '--exclude-forms', metavar='FORMS', help='list of forms to exclude (slugs, separated by commas)'
        )
        parser.add_argument('--delete-forms', action='store_true', help='delete forms after deleting data')

        parser.add_argument(
            '--card-categories',
            metavar='CATEGORIES',
            help='list of card categories (slugs, separated by commas)',
        )
        parser.add_argument('--cards', metavar='FORMS', help='list of cards (slugs, separated by commas)')
        parser.add_argument(
            '--exclude-cards', metavar='FORMS', help='list of cards to exclude (slugs, separated by commas)'
        )
        parser.add_argument('--delete-cards', action='store_true', help='delete cards after deleting data')

    def handle(self, *args, **options):
        if not options.get('no_simulate'):
            self.stdout.write('SIMULATION MODE: no actual wiping will happen.\n')
            self.stdout.write('(use --no-simulate after checking results)\n\n')

        for domain in self.get_domains(**options):
            self.init_tenant_publisher(domain, register_tld_names=False)

            selected_formdefs = []

            for klass, category_klass, param_name in (
                (FormDef, Category, 'form'),
                (CardDef, CardDefCategory, 'card'),
            ):
                if options.get('all'):
                    formdefs = klass.select(order_by='url_name')
                elif options.get(f'{param_name}s') or options.get(f'{param_name}_categories'):
                    formdefs = []
                    if options.get(f'{param_name}_categories'):
                        category_ids = [
                            category_klass.get_by_slug(x).id
                            for x in options[f'{param_name}_categories'].split(',')
                        ]
                        formdefs.extend([x for x in klass.select() if x.category_id in category_ids])
                    if options.get(f'{param_name}s'):
                        formdefs.extend(
                            [klass.get_by_urlname(x) for x in options[f'{param_name}s'].split(',')]
                        )
                else:
                    formdefs = []
                if options.get(f'exclude_{param_name}s'):
                    formdefs = [
                        x for x in formdefs if x.url_name not in options[f'exclude_{param_name}s'].split(',')
                    ]

                selected_formdefs.extend(formdefs)

            for formdef in selected_formdefs:
                if options.get('no_simulate'):
                    formdef.data_class().wipe()
                    if (options.get('delete_forms') and formdef.xml_root_node == 'formdef') or (
                        options.get('delete_cards') and formdef.xml_root_node == 'carddef'
                    ):
                        formdef.remove_self()
                else:
                    count = formdef.data_class().count()
                    if count:
                        self.stdout.write(f'{formdef.item_name} - {formdef.url_name}: {count}\n')
