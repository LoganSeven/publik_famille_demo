# lingo - payment and billing system
# Copyright (C) 2023  Entr'ouvert
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

public_urlpatterns = [
    path('', views.basket_detail, name='lingo-basket-detail'),
    path('validate/', views.basket_validate, name='lingo-basket-validate'),
    path('confirmation/', views.basket_confirmation, name='lingo-basket-confirmation'),
    path('cancel/', views.basket_cancel, name='lingo-basket-cancel'),
    path('status.js', views.basket_status_js, name='lingo-basket-status-js'),
]
