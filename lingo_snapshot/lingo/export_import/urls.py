# lingo - payment and billing system
# Copyright (C) 2022-2024  Entr'ouvert
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

from . import api_views

urlpatterns = [
    path('export-import/', api_views.index, name='api-export-import'),
    path('export-import/bundle-check/', api_views.bundle_check),
    path('export-import/bundle-declare/', api_views.bundle_declare),
    path('export-import/bundle-import/', api_views.bundle_import),
    path('export-import/unlink/', api_views.bundle_unlink),
    path('export-import/uninstall/', api_views.bundle_uninstall),
    path('export-import/uninstall-check/', api_views.bundle_uninstall_check),
    path(
        'export-import/<slug:component_type>/',
        api_views.list_components,
        name='api-export-import-components-list',
    ),
    path(
        'export-import/<slug:component_type>/<slug:slug>/',
        api_views.export_component,
        name='api-export-import-component-export',
    ),
    path(
        'export-import/<slug:component_type>/<slug:slug>/dependencies/',
        api_views.component_dependencies,
        name='api-export-import-component-dependencies',
    ),
    path(
        'export-import/<slug:component_type>/<slug:slug>/redirect/',
        api_views.component_redirect,
        name='api-export-import-component-redirect',
    ),
]
