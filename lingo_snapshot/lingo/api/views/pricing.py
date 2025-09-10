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

from django.utils.translation import gettext_noop as N_
from django_filters import rest_framework as filters
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView

from lingo.api import serializers
from lingo.api.utils import APIAdmin, APIErrorBadRequest, Response
from lingo.pricing.models import Pricing


class PricingFilter(filters.FilterSet):
    date_start = filters.DateFilter(method='do_nothing')
    date_end = filters.DateFilter(method='do_nothing')

    class Meta:
        model = Pricing
        fields = [
            'flat_fee_schedule',
            'subscription_required',
            'date_start',
            'date_end',
        ]

    def do_nothing(self, queryset, name, value):
        return queryset

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        overlaps = {k: self.form.cleaned_data[k] for k in ['date_start', 'date_end']}
        if any(overlaps.values()):
            if not all(overlaps.values()):
                missing = [k for k, v in overlaps.items() if not v][0]
                not_missing = [k for k, v in overlaps.items() if v][0]
                raise ValidationError(
                    {missing: N_('This filter is required when using "%s" filter.') % not_missing}
                )
            queryset = queryset.extra(
                where=['(date_start, date_end) OVERLAPS (%s, %s)'],
                params=[
                    self.form.cleaned_data['date_start'],
                    self.form.cleaned_data['date_end'],
                ],
            )
        return queryset


class Pricings(ListAPIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.PricingSerializer
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = PricingFilter

    def get_queryset(self):
        return Pricing.objects.all().order_by('flat_fee_schedule', 'date_start', 'date_end')

    def get(self, request, format=None):
        try:
            pricings = self.filter_queryset(self.get_queryset())
        except ValidationError as e:
            raise APIErrorBadRequest(N_('invalid filters'), errors=e.detail)

        serializer = self.serializer_class(pricings, many=True)
        return Response({'err': 0, 'data': serializer.data})


pricings = Pricings.as_view()


class PricingCompute(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.PricingComputeSerializer

    def get(self, request, format=None):
        serializer = self.serializer_class(data=request.query_params)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        return Response({'data': serializer.compute(self.request)})

    def post(self, request, format=None):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        return Response({'data': serializer.compute(self.request)})


pricing_compute = PricingCompute.as_view()
