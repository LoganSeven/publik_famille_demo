# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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

from . import views

urlpatterns = [
    path('authenticators/', views.authenticators, name='a2-manager-authenticators'),
    path(
        'authenticators/add/',
        views.add,
        name='a2-manager-authenticator-add',
    ),
    path(
        'authenticators/<int:pk>/detail/',
        views.detail,
        name='a2-manager-authenticator-detail',
    ),
    path(
        'authenticators/<int:pk>/edit/',
        views.edit,
        name='a2-manager-authenticator-edit',
    ),
    path(
        'authenticators/<int:pk>/delete/',
        views.delete,
        name='a2-manager-authenticator-delete',
    ),
    path(
        'authenticators/<int:pk>/toggle/',
        views.toggle,
        name='a2-manager-authenticator-toggle',
    ),
    path(
        'authenticators/<int:pk>/journal/',
        views.journal,
        name='a2-manager-authenticator-journal',
    ),
    path(
        'authenticators/<int:pk>/login-journal/',
        views.login_journal,
        name='a2-manager-authenticator-login-journal',
    ),
    path('authenticators/<int:pk>/export/', views.export_json, name='a2-manager-authenticator-export'),
    path('authenticators/import/', views.import_json, name='a2-manager-authenticator-import'),
    path(
        'authenticators/order/',
        views.order,
        name='a2-manager-authenticators-order',
    ),
    path(
        'authenticators/<int:authenticator_pk>/<slug:model_name>/add/',
        views.add_related_object,
        name='a2-manager-authenticators-add-related-object',
    ),
    path(
        'authenticators/<int:authenticator_pk>/<slug:model_name>/<int:pk>/edit/',
        views.edit_related_object,
        name='a2-manager-authenticators-edit-related-object',
    ),
    path(
        'authenticators/<int:authenticator_pk>/<slug:model_name>/<int:pk>/delete/',
        views.delete_related_object,
        name='a2-manager-authenticators-delete-related-object',
    ),
]
