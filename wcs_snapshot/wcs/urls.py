# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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

from django.urls import path, re_path

from . import api, api_export_import, compat, views
from .statistics import views as statistics_views

urlpatterns = [
    re_path(r'^robots.txt$', views.robots_txt),
    re_path(r'^i18n\.js$', views.i18n_js),
    re_path(r'^backoffice/', views.backoffice),
    path('__provision__/', api.provisionning),
    path('api/export-import/', api_export_import.index, name='api-export-import'),
    path('api/export-import/bundle-check/', api_export_import.bundle_check),
    path('api/export-import/bundle-import/', api_export_import.bundle_import),
    path('api/export-import/bundle-declare/', api_export_import.bundle_declare),
    path('api/export-import/uninstall-check/', api_export_import.uninstall_check),
    path('api/export-import/uninstall/', api_export_import.uninstall),
    path('api/export-import/unlink/', api_export_import.unlink),
    re_path(
        r'^api/export-import/(?P<objects>[\w-]+)/$',
        api_export_import.objects_list,
        name='api-export-import-objects-list',
    ),
    re_path(
        r'^api/export-import/(?P<objects>[\w-]+)/(?P<slug>[\w_-]+)/$',
        api_export_import.object_export,
        name='api-export-import-object-export',
    ),
    re_path(
        r'^api/export-import/(?P<objects>[\w-]+)/(?P<slug>[\w_-]+)/dependencies/$',
        api_export_import.object_dependencies,
        name='api-export-import-object-dependencies',
    ),
    re_path(
        r'^api/export-import/(?P<objects>[\w-]+)/(?P<slug>[\w_-]+)/redirect/$',
        api_export_import.object_redirect,
        name='api-export-import-object-redirect',
    ),
    path('api/validate-condition', api.validate_condition, name='api-validate-condition'),
    path('api/reverse-geocoding', api.reverse_geocoding, name='api-reverse-geocoding'),
    path('api/geocoding', api.geocoding, name='api-geocoding'),
    path('api/statistics/', statistics_views.IndexView.as_view()),
    path(
        'api/statistics/forms/count/',
        statistics_views.FormsCountView.as_view(),
        name='api-statistics-forms-count',
    ),
    path(
        'api/statistics/cards/count/',
        statistics_views.CardsCountView.as_view(),
        name='api-statistics-cards-count',
    ),
    path(
        'api/statistics/resolution-time/',
        statistics_views.ResolutionTimeView.as_view(),
        name='api-statistics-resolution-time',
    ),
    path(
        'api/statistics/resolution-time-cards/',
        statistics_views.CardsResolutionTimeView.as_view(),
        name='api-statistics-resolution-time-cards',
    ),
    # provide django.contrib.auth view names for compatibility with
    # templates created for classic django applications.
    path('login/', compat.quixote, name='auth_login'),
    path('logout', compat.quixote, name='auth_logout'),
]

# other URLs are handled by the quixote handler
urlpatterns.append(re_path(r'', compat.quixote, name='quixote'))
