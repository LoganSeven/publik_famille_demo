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

from django.contrib.auth import get_user_model

from authentic2 import app_settings


def get_user_queryset():
    User = get_user_model()

    qs = User.objects.all()

    qs = qs.filter()

    if app_settings.A2_USER_FILTER:
        qs = qs.filter(**app_settings.A2_USER_FILTER)

    if app_settings.A2_USER_EXCLUDE:
        qs = qs.exclude(**app_settings.A2_USER_EXCLUDE)

    return qs


def is_user_authenticable(user):
    # if user is None, don't care about the authenticable status
    if user is None:
        return True
    if not app_settings.A2_USER_FILTER and not app_settings.A2_USER_EXCLUDE:
        return True
    return get_user_queryset().filter(pk=user.pk).exists()
