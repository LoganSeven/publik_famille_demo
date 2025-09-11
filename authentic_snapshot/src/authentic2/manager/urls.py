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

from django.urls import path, re_path
from django.views.i18n import JavaScriptCatalog

from authentic2.apps.authenticators.manager_urls import urlpatterns as authenticator_urlpatterns
from authentic2_idp_oidc.manager.urls import urlpatterns as oidc_manager_urlpatterns

from ..decorators import required
from . import apiclient_views, journal_views, ou_views, role_views, service_views, user_views, utils, views

urlpatterns = required(
    utils.manager_login_required,
    [
        # homepage
        path('', views.homepage, name='a2-manager-homepage'),
        path('me/', user_views.me, name='a2-manager-me'),
        # Authentic2 users
        path('users/', user_views.users, name='a2-manager-users'),
        re_path(r'^users/export/(?P<format>csv)/$', user_views.users_export, name='a2-manager-users-export'),
        path(
            r'users/export/<a2_userimport_id:uuid>/progress/',
            user_views.users_export_progress,
            name='a2-manager-users-export-progress',
        ),
        path(
            r'users/export/<a2_userimport_id:uuid>/',
            user_views.users_export_file,
            name='a2-manager-users-export-file',
        ),
        path('users/add/', user_views.user_add_default_ou, name='a2-manager-user-add-default-ou'),
        path('users/add/choose-ou/', user_views.user_add_choose_ou, name='a2-manager-user-add-choose-ou'),
        path('users/import/', user_views.user_imports, name='a2-manager-users-imports'),
        path(
            r'users/import/<a2_userimport_id:uuid>/download/<path:filename>',
            user_views.user_import,
            name='a2-manager-users-import-download',
        ),
        path(
            r'users/import/<a2_userimport_id:uuid>/', user_views.user_import, name='a2-manager-users-import'
        ),
        path(
            r'users/import/<a2_userimport_id:import_uuid>/<a2_userimport_id:report_uuid>/',
            user_views.user_import_report,
            name='a2-manager-users-import-report',
        ),
        path(
            'users/advanced/',
            user_views.users_advanced_configuration_view,
            name='a2-manager-users-advanced-configuration',
        ),
        path('users/<int:ou_pk>/add/', user_views.user_add, name='a2-manager-user-add'),
        path('users/<int:pk>/', user_views.user_detail, name='a2-manager-user-detail'),
        path('users/<int:pk>/edit/', user_views.user_edit, name='a2-manager-user-edit'),
        path('users/<int:pk>/delete/', user_views.user_delete, name='a2-manager-user-delete'),
        path('users/<int:pk>/roles/', user_views.roles, name='a2-manager-user-roles'),
        path(
            'users/<int:pk>/change-password/',
            user_views.user_change_password,
            name='a2-manager-user-change-password',
        ),
        path(
            'users/<int:pk>/change-email/',
            user_views.user_change_email,
            name='a2-manager-user-change-email',
        ),
        path('users/<int:pk>/su/', user_views.su, name='a2-manager-user-su'),
        path(
            'users/<int:pk>/authorizations/',
            user_views.user_authorizations,
            name='a2-manager-user-authorizations',
        ),
        path('users/<int:pk>/journal/', user_views.user_journal, name='a2-manager-user-journal'),
        # by uuid
        path(
            r'users/uuid:<user_uuid:slug>/',
            user_views.user_detail,
            name='a2-manager-user-by-uuid-detail',
        ),
        path(
            r'users/uuid:<user_uuid:slug>/edit/',
            user_views.user_edit,
            name='a2-manager-user-by-uuid-edit',
        ),
        path('users/uuid:<user_uuid:slug>/roles/', user_views.roles, name='a2-manager-user-by-uuid-roles'),
        path(
            r'users/uuid:<user_uuid:slug>/change-password/',
            user_views.user_change_password,
            name='a2-manager-user-by-uuid-change-password',
        ),
        path(
            'users/uuid:<user_uuid:slug>/change-email/',
            user_views.user_change_email,
            name='a2-manager-user-by-uuid-change-email',
        ),
        path(
            r'users/uuid:<user_uuid:slug>/journal/',
            user_views.user_journal,
            name='a2-manager-user-journal',
        ),
        # Authentic2 roles
        path('roles/', role_views.listing, name='a2-manager-roles'),
        path('roles/import/', role_views.roles_import, name='a2-manager-roles-import'),
        path('roles/csv-import/', role_views.roles_csv_import, name='a2-manager-roles-csv-import'),
        path(
            'roles/csv-import-sample/',
            role_views.roles_csv_import_sample,
            name='a2-manager-roles-csv-import-sample',
        ),
        path('roles/add/', role_views.add, name='a2-manager-role-add'),
        re_path(r'^roles/export/(?P<format>csv|json)/$', role_views.export, name='a2-manager-roles-export'),
        path('roles/journal/', role_views.roles_journal, name='a2-manager-roles-journal'),
        path('roles/<int:pk>/', role_views.members, name='a2-manager-role-members'),
        path(r'roles/uuid:<user_uuid:slug>/', role_views.members, name='a2-manager-roles-by-uuid-detail'),
        path('roles/<int:pk>/children/', role_views.children, name='a2-manager-role-children'),
        path('roles/<int:pk>/parents/', role_views.parents, name='a2-manager-role-parents'),
        path(
            'roles/<int:pk>/add-admin-user/',
            role_views.add_admin_user,
            name='a2-manager-role-add-admin-user',
        ),
        path(
            'roles/<int:pk>/remove-admin-user/<int:user_pk>/',
            role_views.remove_admin_user,
            name='a2-manager-role-remove-admin-user',
        ),
        path(
            'roles/<int:pk>/add-admin-role/',
            role_views.add_admin_role,
            name='a2-manager-role-add-admin-role',
        ),
        path(
            'roles/<int:pk>/remove-admin-role/<int:role_pk>/',
            role_views.remove_admin_role,
            name='a2-manager-role-remove-admin-role',
        ),
        re_path(
            r'^roles/(?P<pk>\d+)/export/(?P<format>csv)/$',
            role_views.members_export,
            name='a2-manager-role-members-export',
        ),
        path('roles/<int:pk>/delete/', role_views.delete, name='a2-manager-role-delete'),
        path('roles/<int:pk>/edit/', role_views.edit, name='a2-manager-role-edit'),
        path('roles/<int:pk>/journal/', role_views.journal, name='a2-manager-role-journal'),
        re_path(
            r'^roles/(?P<pk>\d+)/user-or-role-select2.json$',
            role_views.user_or_role_select2,
            name='user-or-role-select2-json',
        ),
        path('roles/<int:pk>/summary/', role_views.summary, name='a2-manager-role-summary'),
        # Authentic2 organizational units
        path('organizational-units/', ou_views.listing, name='a2-manager-ous'),
        path('organizational-units/add/', ou_views.add, name='a2-manager-ou-add'),
        path('organizational-units/<int:pk>/', ou_views.detail, name='a2-manager-ou-detail'),
        path('organizational-units/<int:pk>/edit/', ou_views.edit, name='a2-manager-ou-edit'),
        path('organizational-units/<int:pk>/delete/', ou_views.delete, name='a2-manager-ou-delete'),
        re_path(
            r'^organizational-units/export/(?P<format>json)/$', ou_views.export, name='a2-manager-ou-export'
        ),
        path('organizational-units/import/', ou_views.ous_import, name='a2-manager-ous-import'),
        # Services
        path('services/', service_views.listing, name='a2-manager-services'),
        path('services/settings/', service_views.services_settings, name='a2-manager-services-settings'),
        path('services/<int:service_pk>/', service_views.service_detail, name='a2-manager-service'),
        path(
            'services/<int:service_pk>/settings/',
            service_views.service_settings,
            name='a2-manager-service-settings',
        ),
        path(
            'services/<int:service_pk>/settings/edit/',
            service_views.edit_service,
            name='a2-manager-service-settings-edit',
        ),
        path(
            'services/<int:service_pk>/delete/',
            service_views.delete_service,
            name='a2-manager-service-delete',
        ),  # Journal
        path('journal/', journal_views.journal, name='a2-manager-journal'),
        path(
            'journal/event-types/',
            journal_views.journal_event_types,
            name='a2-manager-journal-event-types',
        ),
        # backoffice menu as json
        re_path(r'^menu.json$', views.menu_json),
        # general management
        path('site-export/', views.site_export, name='a2-manager-site-export'),
        path('site-import/', views.site_import, name='a2-manager-site-import'),
        # technical information including ldap config
        path('tech-info/', views.tech_info, name='a2-manager-tech-info'),
        path('api-clients/', apiclient_views.listing, name='a2-manager-api-clients'),
        path('api-clients/add/', apiclient_views.add, name='a2-manager-api-client-add'),
        path('api-clients/<int:pk>/', apiclient_views.detail, name='a2-manager-api-client-detail'),
        path('api-clients/<int:pk>/edit/', apiclient_views.edit, name='a2-manager-api-client-edit'),
        path('api-clients/<int:pk>/delete/', apiclient_views.delete, name='a2-manager-api-client-delete'),
    ]
    + authenticator_urlpatterns,
)

urlpatterns += oidc_manager_urlpatterns

urlpatterns += [
    path(
        'jsi18n/',
        JavaScriptCatalog.as_view(packages=['authentic2.manager']),
        name='a2-manager-javascript-catalog',
    ),
    re_path(r'^select2.json$', views.select2, name='django_select2-json'),
]
