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

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import FormView

from authentic2 import data_transfer
from authentic2.a2_rbac.models import OrganizationalUnit

from . import forms, tables, views


class OrganizationalUnitView(views.BaseTableView):
    template_name = 'authentic2/manager/ous.html'
    model = OrganizationalUnit
    table_class = tables.OUTable
    search_form_class = forms.NameSearchForm
    permissions = ['a2_rbac.search_organizationalunit']
    title = _('Organizational units')


listing = OrganizationalUnitView.as_view()


class OrganizationalUnitAddView(views.BaseAddView):
    model = OrganizationalUnit
    permissions = ['a2_rbac.add_organizationalunit']
    form_class = forms.OUEditForm
    title = _('Add organizational unit')
    fields = ('name',)

    def get_success_url(self):
        return '..'


add = OrganizationalUnitAddView.as_view()


class OrganizationalUnitDetailView(views.BaseDetailView):
    model = OrganizationalUnit
    permissions = ['a2_rbac.view_organizationalunit']
    form_class = forms.OUEditForm
    template_name = 'authentic2/manager/ou_detail.html'

    @property
    def title(self):
        return str(self.object)

    def authorize(self, request, *args, **kwargs):
        super().authorize(request, *args, **kwargs)
        self.can_delete = self.can_delete and not self.object.default


detail = OrganizationalUnitDetailView.as_view()


class OrganizationalUnitEditView(views.BaseEditView):
    model = OrganizationalUnit
    permissions = ['a2_rbac.change_organizationalunit']
    form_class = forms.OUEditForm
    template_name = 'authentic2/manager/ou_edit.html'
    title = _('Edit organizational unit')


edit = OrganizationalUnitEditView.as_view()


class OrganizationalUnitDeleteView(views.BaseDeleteView):
    model = OrganizationalUnit
    template_name = 'authentic2/manager/ou_delete.html'
    permissions = ['a2_rbac.delete_organizationalunit']
    title = _('Delete organizational unit')

    def dispatch(self, request, *args, **kwargs):
        if self.get_object().default:
            messages.warning(
                request,
                _(
                    'You cannot delete the default organizational unit, you must first set another default'
                    ' organiational unit.'
                ),
            )
            return self.return_ajax_response(request, HttpResponseRedirect(self.get_success_url()))
        return super().dispatch(request, *args, **kwargs)


delete = OrganizationalUnitDeleteView.as_view()


class OusExportView(views.ExportMixin, OrganizationalUnitView):
    export_prefix = 'ous-export-'

    def get(self, request, *args, **kwargs):
        export = data_transfer.export_site(
            data_transfer.ExportContext(ou_qs=self.get_table_data(), export_roles=False, export_ous=True)
        )
        return self.export_response(json.dumps(export, indent=4), 'application/json', 'json')


export = OusExportView.as_view()


class OusImportView(
    views.PermissionMixin, views.TitleMixin, views.MediaMixin, views.FormNeedsRequest, FormView
):
    form_class = forms.OusImportForm
    model = OrganizationalUnit
    template_name = 'authentic2/manager/import_form.html'
    title = _('Organizational Units Import')

    def post(self, request, *args, **kwargs):
        if not self.can_add:
            raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            context = data_transfer.ImportContext(import_roles=False, request=self.request)
            with transaction.atomic():
                data_transfer.import_site(form.cleaned_data['site_json'], context)
        except ValidationError as e:
            form.add_error('site_json', e)
            return self.form_invalid(form)

        return super().form_valid(form)

    def get_success_url(self):
        messages.success(self.request, _('Organizational Units have been successfully imported.'))
        return reverse('a2-manager-ous')


ous_import = OusImportView.as_view()
