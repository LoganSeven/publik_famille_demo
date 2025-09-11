# authentic2 - versatile identity manager
# Copyright (C) 2022p Entr'ouvert
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


from django.urls import path

from authentic2.decorators import required
from authentic2.manager.utils import manager_login_required

from . import views

urlpatterns = required(
    manager_login_required,
    [
        path('services/add-oidc/', views.add_oidc_service, name='a2-manager-add-oidc-service'),
        path(
            'services/<int:service_pk>/claim/add/',
            views.oidc_claim_add,
            name='a2-manager-oidc-claim-add',
        ),
        path(
            'services/<int:service_pk>/claim/<int:claim_pk>/edit/',
            views.oidc_claim_edit,
            name='a2-manager-oidc-claim-edit',
        ),
        path(
            'services/<int:service_pk>/claim/<int:claim_pk>/delete/',
            views.oidc_claim_delete,
            name='a2-manager-oidc-claim-delete',
        ),
        path(
            'services/generate_uuid', views.ServicesGenerateUUIDView, name='a2-manager-service-generate-uuid'
        ),
    ],
)
