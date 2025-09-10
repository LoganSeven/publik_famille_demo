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

from django.db.models import Count, Sum
from django.db.models.functions import TruncDay
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop as N_
from django.utils.translation import pgettext
from rest_framework.views import APIView

from lingo.agendas.models import Agenda
from lingo.api.serializers import MEASURE_CHOICES, StatisticsFiltersSerializer
from lingo.api.utils import APIAdmin, APIErrorBadRequest, Response
from lingo.invoicing.models import Invoice, InvoiceLine, Regie


class StatisticsList(APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, *args, **kwargs):
        regie_options = [{'id': '_all', 'label': pgettext('regies', 'All')}] + [
            {'id': x.slug, 'label': x.label} for x in Regie.objects.all()
        ]
        activity_options = [{'id': '_all', 'label': pgettext('activity', 'All')}] + [
            {'id': x.slug, 'label': x.label} for x in Agenda.objects.all()
        ]
        invoices = sorted(Invoice.objects.distinct('payer_external_id'), key=lambda x: x.payer_name)
        payer_options = [{'id': '_all', 'label': pgettext('payer', 'All')}] + [
            {'id': x.payer_external_id, 'label': x.payer_name} for x in invoices
        ]

        return Response(
            {
                'data': [
                    {
                        'name': _('Invoice'),
                        'url': request.build_absolute_uri(reverse('api-statistics-invoice')),
                        'id': 'invoice',
                        'filters': [
                            {
                                'id': 'time_interval',
                                'label': _('Interval'),
                                'options': [{'id': 'day', 'label': _('Day')}],
                                'required': True,
                                'default': 'day',
                            },
                            {
                                'id': 'measures',
                                'label': _('Measures'),
                                'options': [{'id': x, 'label': y} for x, y in MEASURE_CHOICES.items()],
                                'required': True,
                                'multiple': True,
                                'default': 'total_amount',
                            },
                            {
                                'id': 'regie',
                                'label': _('Regie'),
                                'options': regie_options,
                                'required': True,
                                'default': '_all',
                            },
                            {
                                'id': 'activity',
                                'label': _('Activity'),
                                'options': activity_options,
                                'required': True,
                                'default': '_all',
                            },
                            {
                                'id': 'payer_external_id',
                                'label': _('Payer'),
                                'options': payer_options,
                                'required': True,
                                'default': '_all',
                            },
                        ],
                    }
                ]
            }
        )


statistics_list = StatisticsList.as_view()


class InvoiceStatistics(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = StatisticsFiltersSerializer

    def get(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.query_params)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid statistics filters'), errors=serializer.errors)
        data = serializer.validated_data

        invoices = Invoice.objects.filter(cancelled_at__isnull=True).exclude(pool__campaign__finalized=False)
        if 'start' in data:
            invoices = invoices.filter(date_publication__gte=data['start'])
        if 'end' in data:
            invoices = invoices.filter(date_publication__lte=data['end'])

        regie_slug = data.get('regie', '_all')
        if regie_slug != '_all':
            invoices = invoices.filter(regie__slug=regie_slug)

        activity_slug = data.get('activity', '_all')
        if activity_slug != '_all':
            lines = InvoiceLine.objects.filter(agenda_slug=activity_slug).values('invoice')
            invoices = invoices.filter(pk__in=lines)

        payer_external_id = data.get('payer_external_id', '_all')
        if payer_external_id != '_all':
            invoices = invoices.filter(payer_external_id=payer_external_id)

        invoices = invoices.annotate(day=TruncDay('date_publication')).values('day').order_by('day')

        aggregates = {}
        for field in data['measures']:
            if field == 'count':
                aggregates['count'] = Count('id')
            else:
                aggregates[field] = Sum(field)
        invoices = invoices.annotate(**aggregates)

        series = []
        if invoices:
            for field in data['measures']:
                series.append(
                    {'label': MEASURE_CHOICES.get(field), 'data': [invoice[field] for invoice in invoices]}
                )

        return Response(
            {
                'data': {
                    'x_labels': [invoice['day'].strftime('%Y-%m-%d') for invoice in invoices],
                    'series': series,
                },
                'err': 0,
            }
        )


invoice_statistics = InvoiceStatistics.as_view()
