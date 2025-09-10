# lingo - payment and billing system
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

import json

from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.template import RequestContext, Template, TemplateSyntaxError, VariableDoesNotExist
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.translation import pgettext_lazy
from django.views.generic import TemplateView, View

from lingo.epayment.models import PaymentBackend
from lingo.invoicing.models import Regie
from lingo.manager.forms import InspectTestTemplateForm
from lingo.pricing.models import Pricing


class HomepageView(TemplateView):
    template_name = 'lingo/manager_homepage.html'

    def has_access(self):
        group_ids = [x.id for x in self.request.user.groups.all()]
        self.backend_access = (
            self.request.user.is_staff
            or PaymentBackend.objects.filter(
                Q(view_role_id__in=group_ids) | Q(edit_role_id__in=group_ids)
            ).exists()
        )
        self.pricing_access = (
            self.request.user.is_staff
            or Pricing.objects.filter(Q(view_role_id__in=group_ids) | Q(edit_role_id__in=group_ids)).exists()
        )
        self.regie_access = (
            self.request.user.is_staff
            or Regie.objects.filter(
                Q(view_role_id__in=group_ids)
                | Q(edit_role_id__in=group_ids)
                | Q(invoice_role_id__in=group_ids)
                | Q(control_role_id__in=group_ids)
            ).exists()
        )
        return self.backend_access or self.pricing_access or self.regie_access

    def get_context_data(self, **kwargs):
        kwargs['backend_access'] = self.backend_access
        kwargs['pricing_access'] = self.pricing_access
        kwargs['regie_access'] = self.regie_access
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        if not self.has_access():
            self.template_name = 'lingo/manager_no_access.html'
        return super().get(request, *args, **kwargs)

    def render_to_response(self, context, **response_kwargs):
        if self.template_name == 'lingo/manager_no_access.html':
            response_kwargs['status'] = 403
        return super().render_to_response(context, **response_kwargs)


homepage = HomepageView.as_view()


class InspectView(TemplateView):
    template_name = 'lingo/manager_inspect.html'

    def has_access(self):
        group_ids = [x.id for x in self.request.user.groups.all()]
        self.pricing_access = (
            self.request.user.is_staff or Pricing.objects.filter(edit_role_id__in=group_ids).exists()
        )
        return self.pricing_access

    def get(self, request, *args, **kwargs):
        if not self.has_access():
            return HttpResponseForbidden()
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['inspect_form'] = InspectTestTemplateForm()
        return super().get_context_data(**kwargs)


inspect = InspectView.as_view()


class InspectTestTemplateView(View):
    def has_access(self):
        group_ids = [x.id for x in self.request.user.groups.all()]
        self.pricing_access = (
            self.request.user.is_staff or Pricing.objects.filter(edit_role_id__in=group_ids).exists()
        )
        return self.pricing_access

    def post(self, request, *args, **kwargs):
        if not self.has_access():
            return HttpResponseForbidden()
        if 'django_template' not in request.POST:
            return HttpResponseBadRequest()

        response = {}
        try:
            template = Template(request.POST['django_template'])
            response['result'] = template.render(RequestContext(request))
        except (TemplateSyntaxError, VariableDoesNotExist) as e:
            response['error'] = str(e)
            return JsonResponse(response)

        return JsonResponse(response)


inspect_test_template = InspectTestTemplateView.as_view()


def menu_json(request):
    label = pgettext_lazy('lingo title', 'Payments')
    json_str = json.dumps(
        [
            {
                'label': force_str(label),
                'slug': 'lingo',
                'url': request.build_absolute_uri(reverse('lingo-manager-homepage')),
            }
        ]
    )
    content_type = 'application/json'
    for variable in ('jsonpCallback', 'callback'):
        if variable in request.GET:
            json_str = '%s(%s);' % (request.GET[variable], json_str)
            content_type = 'application/javascript'
            break
    response = HttpResponse(content_type=content_type)
    response.write(json_str)
    return response
