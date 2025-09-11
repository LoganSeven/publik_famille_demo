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

from . import views

urlpatterns = [
    re_path('^login/?$', views.login, name='a2-idp-cas-login'),
    path('continue/', views._continue, name='a2-idp-cas-continue'),
    re_path('^validate/?$', views.validate, name='a2-idp-cas-validate'),
    re_path('^serviceValidate/?$', views.service_validate, name='a2-idp-cas-service-validate'),
    re_path('^logout/?$', views.logout, name='a2-idp-cas-logout'),
    re_path('^proxy/?$', views.proxy, name='a2-idp-cas-proxy'),
    re_path('^proxyValidate/?$', views.proxy_validate, name='a2-idp-cas-proxy-validate'),
]
