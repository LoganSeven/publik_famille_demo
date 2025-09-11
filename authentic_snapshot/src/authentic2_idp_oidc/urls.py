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

from django.urls import re_path

from . import views

urlpatterns = [
    re_path(
        r'^.well-known/openid-configuration$', views.openid_configuration, name='oidc-openid-configuration'
    ),
    re_path(r'^idp/oidc/certs/?$', views.certs, name='oidc-certs'),
    re_path(r'^idp/oidc/authorize/?$', views.authorize, name='oidc-authorize'),
    re_path(r'^idp/oidc/token/?$', views.token, name='oidc-token'),
    re_path(r'^idp/oidc/revoke/?$', views.revoke, name='oidc-token-revocation'),
    re_path(r'^idp/oidc/user_info/?$', views.user_info, name='oidc-user-info'),
    re_path(r'^idp/oidc/logout/?$', views.logout, name='oidc-logout'),
]
