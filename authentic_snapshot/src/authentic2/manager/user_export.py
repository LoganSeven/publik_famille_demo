# authentic2 - versatile identity manager
# Copyright (C) 2010-2021 Entr'ouvert
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
import datetime
import os

import tablib
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.db import transaction

from authentic2.manager.resources import EscapeFormulaMixin, UserResource
from authentic2.models import Attribute, AttributeValue
from authentic2.utils.crypto import new_base64url_id


def get_user_dataset(qs):
    user_resource = UserResource()
    fields = user_resource._meta.export_order + ('email_verified', 'is_active', 'modified')
    attributes = [attr.name for attr in Attribute.objects.all()]
    headers = fields + tuple('attribute_%s' % attr for attr in attributes)

    at_mapping = {a.id: a for a in Attribute.objects.all()}
    avs = (
        AttributeValue.objects.filter(content_type=ContentType.objects.get_for_model(get_user_model()))
        .filter(attribute__disabled=False)
        .values()
    )

    user_attrs = collections.defaultdict(dict)
    for av in avs:
        user_attrs[av['object_id']][at_mapping[av['attribute_id']].name] = av['content']

    def iso(rec):
        if rec is None or rec == {}:
            return ''
        if hasattr(rec, 'strftime'):
            if isinstance(rec, datetime.datetime):
                _format = '%Y-%m-%d %H:%M:%S'
            else:
                _format = '%Y-%m-%d'
            return rec.strftime(_format)
        return rec

    def create_record(user):
        record = []

        def append_value(value):
            record.append(EscapeFormulaMixin.escape(value))

        for field in fields:
            if field == 'roles':
                value = user_resource.dehydrate_roles(user)
            else:
                value = getattr(user, field)
            append_value(value)

        attr_d = user_attrs[user.pk]
        for attr in attributes:
            append_value(attr_d.get(attr))

        return [iso(x) for x in record]

    dataset = tablib.Dataset(headers=headers)
    for user in qs:
        dataset.append(create_record(user))
    return dataset


class UserExport:
    def __init__(self, uuid):
        self.uuid = uuid
        self.path = os.path.join(self.base_path(), self.uuid)
        self.export_path = os.path.join(self.path, 'export.csv')
        self.progress_path = os.path.join(self.path, 'progress')

    @classmethod
    def base_path(cls):
        path = default_storage.path('user_exports')
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    @property
    def exists(self):
        return os.path.exists(self.path)

    @classmethod
    def new(cls):
        export = cls(new_base64url_id())
        os.makedirs(export.path)
        return export

    @property
    def csv(self):
        return open(self.export_path)

    def set_export_content(self, content):
        with open(self.export_path, 'w') as f:
            f.write(content)

    @property
    def progress(self):
        progress = 0
        if os.path.exists(self.progress_path):
            with open(self.progress_path) as f:
                progress = f.read()
        return int(progress) if progress else 0

    def set_progress(self, progress):
        with open(self.progress_path, 'w') as f:
            f.write(str(progress))


def export_users_to_file(uuid, query):
    export = UserExport(uuid)
    qs = get_user_model().objects.all()
    qs.query = query
    qs = qs.select_related('ou')
    qs = qs.prefetch_related('roles', 'roles__parent_relation__parent')
    count = qs.count() or 1

    with transaction.atomic(savepoint=False):
        qs.set_trigram_similarity_threshold()
        ids = qs.values_list('id', flat=True)

    def paginate_queryset(ids):
        progress = 0
        while ids:
            yield from qs.filter(id__in=ids[:1000])
            progress += min(len(ids), 1000)
            export.set_progress(min(round(progress / count * 100), 99))
            ids = ids[1000:]

    dataset = get_user_dataset(paginate_queryset(ids))

    if hasattr(dataset, 'csv'):
        # compatiblity for tablib < 0.11
        csv = dataset.csv
    else:
        csv = dataset.export('csv')
    export.set_export_content(csv)
    export.set_progress(100)
