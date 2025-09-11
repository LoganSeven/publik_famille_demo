# authentic2-auth-fc - authentic2 authentication for FranceConnect
# Copyright (C) 2019 Entr'ouvert
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
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from authentic2.api_views import DjangoPermission
from authentic2.compat.drf import action


@action(
    detail=True,
    methods=['delete'],
    url_path='fc-unlink',
    permission_classes=(DjangoPermission('custom_user.view_user'),),
)
def fc_unlink(self, request, uuid):
    user = get_object_or_404(get_user_model(), uuid=uuid)
    if hasattr(user, 'fc_account'):
        user.fc_account.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
