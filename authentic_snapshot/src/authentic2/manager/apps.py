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

from django.apps import AppConfig as BaseAppConfig


class AppConfig(BaseAppConfig):
    name = 'authentic2.manager'
    verbose_name = 'Authentic2 Manager'

    def ready(self):
        from django.db.models.signals import post_save

        from authentic2.a2_rbac.models import OrganizationalUnit

        post_save.connect(self.post_save_ou, sender=OrganizationalUnit)

        from authentic2.passwords import init_password_dictionaries

        init_password_dictionaries()

    def post_save_ou(self, *args, **kwargs):
        from . import utils

        utils.get_ou_count.cache.clear()
