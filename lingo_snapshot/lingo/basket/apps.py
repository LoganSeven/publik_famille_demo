# lingo - payment and billing system
# Copyright (C) 2023  Entr'ouvert
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

import django.apps
from django.utils.translation import gettext_lazy as _


def user_get_name_id(user):
    if not hasattr(user, '_name_id'):
        user._name_id = None
        saml_identifier = user.saml_identifiers.first()
        if saml_identifier:
            user._name_id = saml_identifier.name_id

    return user._name_id


class AppConfig(django.apps.AppConfig):
    name = 'lingo.basket'
    verbose_name = _('Basket')

    def ready(self):
        from django.contrib.auth import get_user_model

        get_user_model().add_to_class('get_name_id', user_get_name_id)
