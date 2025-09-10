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

import collections

from django.db import transaction

from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.pricing.models import CriteriaCategory, Pricing


def export_site(
    agendas=True,
    check_type_groups=True,
    pricing_categories=True,
    pricings=True,
):
    '''Dump site objects to JSON-dumpable dictionnary'''
    data = {}
    if pricings:
        data['pricings'] = [x.export_json() for x in Pricing.objects.all()]
    if pricing_categories:
        data['pricing_categories'] = [x.export_json() for x in CriteriaCategory.objects.all()]
    if check_type_groups:
        data['check_type_groups'] = [x.export_json() for x in CheckTypeGroup.objects.all()]
    if agendas:
        data['agendas'] = [x.export_json() for x in Agenda.objects.all()]
    return data


def import_site(data):
    results = {
        key: collections.defaultdict(list)
        for key in [
            'agendas',
            'check_type_groups',
            'pricing_categories',
            'pricings',
        ]
    }

    with transaction.atomic():
        for cls, key in (
            (CriteriaCategory, 'pricing_categories'),
            (CheckTypeGroup, 'check_type_groups'),
            (Agenda, 'agendas'),
            (Pricing, 'pricings'),
        ):
            objs = data.get(key, [])
            for obj in objs:
                created, obj = cls.import_json(obj)
                results[key]['all'].append(obj)
                if created:
                    results[key]['created'].append(obj)
                else:
                    results[key]['updated'].append(obj)
    return results
