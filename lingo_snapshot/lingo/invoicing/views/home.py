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

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_str
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import FormView

from lingo.invoicing.forms import ExportForm, ImportForm
from lingo.invoicing.utils import export_site, import_site
from lingo.utils.misc import LingoImportError, json_dump


class ConfigExportView(FormView):
    form_class = ExportForm
    template_name = 'lingo/invoicing/export.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = HttpResponse(content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename="export_invoicing_config_{}.json"'.format(
            now().strftime('%Y%m%d')
        )
        json_dump(export_site(**form.cleaned_data), response, indent=2)
        return response


config_export = ConfigExportView.as_view()


class ConfigImportView(FormView):
    form_class = ImportForm
    template_name = 'lingo/invoicing/import.html'
    success_url = reverse_lazy('lingo-manager-invoicing-regie-list')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            config_json = json.loads(force_str(self.request.FILES['config_json'].read()))
        except ValueError:
            form.add_error('config_json', _('File is not in the expected JSON format.'))
            return self.form_invalid(form)

        try:
            results = import_site(config_json)
        except LingoImportError as exc:
            form.add_error('config_json', '%s' % exc)
            return self.form_invalid(form)
        except KeyError as exc:
            form.add_error('config_json', _('Key "%s" is missing.') % exc.args[0])
            return self.form_invalid(form)

        import_messages = {
            'regies': {
                'create_noop': _('No regie created.'),
                'create': lambda x: ngettext(
                    'A regie has been created.',
                    '%(count)d regies have been created.',
                    x,
                ),
                'update_noop': _('No regie updated.'),
                'update': lambda x: ngettext(
                    'A regie has been updated.',
                    '%(count)d regies have been updated.',
                    x,
                ),
            },
        }

        global_noop = True
        for obj_name, obj_results in results.items():
            for obj in obj_results['all']:
                obj.take_snapshot(request=self.request, comment=_('imported'))
            if obj_results['all']:
                global_noop = False
                count = len(obj_results['created'])
                if not count:
                    message1 = import_messages[obj_name].get('create_noop')
                else:
                    message1 = import_messages[obj_name]['create'](count) % {'count': count}

                count = len(obj_results['updated'])
                if not count:
                    message2 = import_messages[obj_name]['update_noop']
                else:
                    message2 = import_messages[obj_name]['update'](count) % {'count': count}

                if message1:
                    obj_results['messages'] = '%s %s' % (message1, message2)
                else:
                    obj_results['messages'] = message2

        r_count = len(results['regies']['all'])
        if r_count == 1:
            # only one regie imported, redirect to regie page
            return HttpResponseRedirect(
                reverse(
                    'lingo-manager-invoicing-regie-detail',
                    kwargs={'pk': results['regies']['all'][0].pk},
                )
            )

        if global_noop:
            messages.info(self.request, _('No data found.'))
        else:
            messages.info(self.request, results['regies']['messages'])

        return super().form_valid(form)


config_import = ConfigImportView.as_view()
