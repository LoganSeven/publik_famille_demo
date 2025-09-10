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

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop as N_
from rest_framework.views import APIView

from lingo.agendas.chrono import refresh_agendas
from lingo.agendas.models import Agenda, AgendaUnlockLog, CheckType
from lingo.api import serializers
from lingo.api.utils import APIAdmin, APIErrorBadRequest, Response
from lingo.invoicing.models import Campaign


class AgendaCheckTypeList(APIView):
    permission_classes = ()

    def get(self, request, agenda_identifier=None, format=None):
        agenda = get_object_or_404(Agenda, slug=agenda_identifier)

        check_types = []
        if agenda.check_type_group:
            check_types = agenda.check_type_group.check_types.filter(disabled=False).prefetch_related(
                'group__agenda_set'
            )
            check_types = serializers.CheckTypeSerializer(check_types, many=True).data

        return Response({'data': check_types})


agenda_check_type_list = AgendaCheckTypeList.as_view()


class AgendasCheckTypeList(APIView):
    permission_classes = ()
    serializer_class = serializers.AgendasCheckTypeListSerializer

    def get(self, request):
        serializer = self.serializer_class(data=request.query_params)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        check_types = (
            CheckType.objects.filter(
                group__agenda__in=list(serializer.validated_data['agendas']),
                disabled=False,
            )
            .select_related('group')
            .prefetch_related('group__agenda_set')
            .distinct()
        )

        return Response({'data': serializers.CheckTypeSerializer(check_types, many=True).data})


agendas_check_type_list = AgendasCheckTypeList.as_view()


class AgendaUnlock(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.AgendaUnlockSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        campaign_qs = Campaign.objects.filter(
            date_start__lt=serializer.validated_data['date_end'],
            date_end__gt=serializer.validated_data['date_start'],
            # exclude corrective campaigns
            primary_campaign__isnull=True,
        )

        for agenda in serializer.validated_data['agendas']:
            for campaign in campaign_qs.filter(agendas=agenda):
                last_log = (
                    AgendaUnlockLog.objects.filter(agenda=agenda, campaign=campaign)
                    .order_by('created_at')
                    .last()
                )
                if last_log and last_log.active:
                    # update updated_at field
                    last_log.save()
                    continue
                AgendaUnlockLog.objects.create(agenda=agenda, campaign=campaign)

        return Response({'err': 0})


agenda_unlock = AgendaUnlock.as_view()


class AgendaDuplicateSettings(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.AgendaDuplicateSettingsSerializer

    def post(self, request, agenda_identifier):
        source_agenda = get_object_or_404(Agenda, slug=agenda_identifier)

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        try:
            target_agenda = Agenda.objects.get(slug=serializer.validated_data['target_agenda'])
        except Agenda.DoesNotExist:
            refresh_agendas(q_slug=serializer.validated_data['target_agenda'])
            try:
                target_agenda = Agenda.objects.get(slug=serializer.validated_data['target_agenda'])
            except Agenda.DoesNotExist:
                raise APIErrorBadRequest(N_('unknown target agenda'))

        for attribute in ('check_type_group_id', 'regie_id'):
            setattr(target_agenda, attribute, getattr(source_agenda, attribute))

        target_agenda.save()
        target_agenda.take_snapshot(comment=_('settings copied from %s') % source_agenda.slug)

        return Response({'err': 0})


agenda_duplicate_settings = AgendaDuplicateSettings.as_view()
