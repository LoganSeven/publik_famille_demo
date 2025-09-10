# lingo - payment and billing system
# Copyright (C) 2022-2023  Entr'ouvert
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

manager_urlpatterns = [
    path('', views.transaction_list, name='lingo-manager-epayment-transaction-list'),
    path('backend/', views.backend_list, name='lingo-manager-epayment-backend-list'),
    path('backend/add/', views.backend_add, name='lingo-manager-epayment-backend-add'),
    path('backend/<int:pk>/', views.backend_detail, name='lingo-manager-epayment-backend-detail'),
    path('backend/<int:pk>/edit/', views.backend_edit, name='lingo-manager-epayment-backend-edit'),
    path('backend/<int:pk>/delete/', views.backend_delete, name='lingo-manager-epayment-backend-delete'),
]

public_urlpatterns = [
    path('pay/demo/', views.pay_demo, name='lingo-epayment-demo'),
    path('pay/invoice/<uuid:invoice_uuid>/', views.pay_invoice_view, name='lingo-epayment-invoice'),
    path('pay/callback/', views.pay_callback, name='lingo-epayment-auto-callback'),
    path('pay/callback/<uuid:transaction_id>/', views.pay_callback, name='lingo-epayment-explicit-callback'),
    path('pay/return/', views.pay_return, name='lingo-epayment-auto-return'),
    path('pay/return/<uuid:transaction_id>/', views.pay_return, name='lingo-epayment-explicit-return'),
    path('pay/processing/<uuid:transaction_id>/', views.payment_processing, name='lingo-epayment-processing'),
    path(
        'api/epayment/status/<uuid:transaction_id>/',
        views.payment_processing_status,
        name='lingo-epayment-processing-status',
    ),
]
