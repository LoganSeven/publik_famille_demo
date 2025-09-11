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

from django.urls import include, path

from . import views

fcpatterns = [
    path('callback/', views.login_or_link, name='fc-login-or-link'),
    path('callback_logout/', views.logout, name='fc-logout'),
]

urlpatterns = [
    path('fc/', include(fcpatterns)),
    path('accounts/fc/unlink/', views.unlink, name='fc-unlink'),
]
