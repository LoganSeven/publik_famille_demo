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


from django.core.management.base import BaseCommand

from authentic2.custom_user.models import User


class Command(BaseCommand):
    help = 'Fix user attributes'

    requires_system_checks = []

    def handle(self, *args, **options):
        user_ids = User.objects.values_list('id', flat=True)

        i = 0
        while True:
            batch = user_ids[i * 100 : i * 100 + 100]
            if not batch:
                break
            users = User.objects.prefetch_related('attribute_values__attribute').filter(id__in=batch)
            count = 0
            for user in users:
                try:
                    atv_first_name = [
                        atv for atv in user.attribute_values.all() if atv.attribute.name == 'first_name'
                    ][0]
                except IndexError:
                    atv_first_name = None
                try:
                    atv_last_name = [
                        atv for atv in user.attribute_values.all() if atv.attribute.name == 'last_name'
                    ][0]
                except IndexError:
                    atv_last_name = None
                save = False
                fixed = True
                if not atv_first_name:
                    fixed = True
                    user.attributes.first_name = user.first_name
                elif atv_first_name.content != user.first_name:
                    user.first_name = atv_first_name.content
                    save = True
                if not atv_last_name:
                    fixed = True
                    user.attributes.last_name = user.last_name
                elif atv_last_name.content != user.last_name:
                    user.last_name = atv_last_name.content
                    save = True
                if save:
                    user.save()
                if save or fixed:
                    count += 1
            i += 1
            print('Fixed %d users.' % count)
