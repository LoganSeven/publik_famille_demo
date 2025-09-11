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

from functools import wraps

from django.utils.functional import lazy

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.utils import misc as utils_misc
from authentic2.utils.cache import GlobalCache


def label_from_user(user):
    labels = []
    if user.first_name or user.last_name:
        labels.append(user.first_name)
        if user.first_name and user.last_name:
            labels.append(' ')
        labels.append(user.last_name)
    if user.email and user.email not in labels:
        if labels:
            labels.append(' - ')
        labels.append(user.email)
    if user.username and user.username not in labels:
        if labels:
            labels.append(' - ')
        labels.append(user.username)
    return ''.join(labels)


@GlobalCache(timeout=10)
def get_ou_count():
    return OrganizationalUnit.objects.count()


def label_from_role(role):
    label = str(role)
    if role.ou and get_ou_count() > 1:
        label = f'{role.ou} - {role}'
    return label


@GlobalCache(timeout=10)
def has_show_username():
    return not OrganizationalUnit.objects.filter(show_username=False).exists()


def manager_login_required(func):
    @wraps(func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated:
            return func(request, *args, **kwargs)
        return utils_misc.login_require(
            request, login_url=lazy(utils_misc.get_manager_login_url, str)(), login_hint=['backoffice']
        )

    return _wrapped_view
