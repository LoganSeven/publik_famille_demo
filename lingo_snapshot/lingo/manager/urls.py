# lingo - billing and payment system
# Copyright (C) 2022  Entr'ouvert
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
    path('', views.homepage, name='lingo-manager-homepage'),
    path('inspect/', views.inspect, name='lingo-manager-inspect'),
    path('inspect/test-template/', views.inspect_test_template, name='lingo-manager-inspect-test-template'),
    re_path(r'^menu.json$', views.menu_json),
]
