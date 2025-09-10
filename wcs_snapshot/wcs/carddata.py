# w.c.s. - web application for online forms
# Copyright (C) 2005-2019  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import json

from quixote import get_publisher, get_request, get_session

from wcs.formdata import FormData

from .qommon import _
from .sql_criterias import Equal, Null, StrictNotEqual


class CardData(FormData):
    def get_data_source_structured_item(
        self, digest_key='default', group_by=None, with_related_urls=False, with_files_urls=False
    ):
        if not self.digests:
            if digest_key == 'default':
                summary = _('Digest (default) not defined')
            else:
                summary = _('Digest (custom view "%s") not defined') % digest_key.replace('custom-view:', '')
            get_publisher().record_error(summary, formdata=self)

        item = {
            'id': self.id_display if self.formdef.id_template else self.id,
            'text': (self.digests or {}).get(digest_key) or '',
        }

        if with_related_urls:
            edit_related_url = self.get_edit_related_url()
            if edit_related_url:
                item['edit_related_url'] = edit_related_url
            view_related_url = self.get_view_related_url()
            if view_related_url:
                item['view_related_url'] = view_related_url

        if group_by:
            item['group_by'] = self.data.get(f'{group_by}_display') or self.data.get(str(group_by))

        for field in self.formdef.get_all_fields():
            if not field.varname or field.varname in ('id', 'text'):
                continue
            value = self.data and self.data.get(field.id)

            if with_files_urls and hasattr(value, 'file_digest'):
                item['%s_url' % field.varname] = self.get_file_by_token_url(value.file_digest())

            if isinstance(value, str):
                item[field.varname] = value
        return item

    def get_edit_related_url(self):
        wf_status = self.get_status()
        if wf_status is None:
            return
        for _item in wf_status.items:
            if not _item.key == 'editable':
                continue
            if not _item.check_auth(self, get_request().user):
                continue
            return (
                self.get_url(
                    backoffice=get_request().is_in_backoffice(),
                    include_category=True,
                    language=get_publisher().current_language,
                )
                + 'wfedit-%s' % _item.id
            )

    def get_view_related_url(self):
        if not self.formdef.is_user_allowed_read(get_request().user, self):
            return
        return self.get_url(backoffice=True)

    def get_display_label(self, digest_key='default'):
        return (self.digests or {}).get(digest_key) or self.get_display_name()

    def get_author_qualification(self):
        return None

    def get_file_base_url(self):
        return '%sdownload' % self.get_api_url()

    def just_created(self):
        super().just_created()
        if self.submission_agent_id:
            self.evolution[0].who = self.submission_agent_id

    @classmethod
    def get_submission_channels(cls):
        return {'web': _('Web'), 'file-import': _('File Import')}

    @classmethod
    def get_by_uuid(cls, value):
        try:
            return cls.select([Equal('uuid', value)], limit=1)[0]
        except IndexError:
            raise KeyError(value)

    def get_file_by_token_url(self, file_digest):
        context = {
            'carddef_slug': self.formdef.url_name,
            'data_id': self.id,
            'file_digest': file_digest,
        }
        token = get_session().create_token('card-file-by-token', context)
        return '/api/card-file-by-token/%s' % token.id

    def update_related(self):
        if self.is_draft():
            return
        if self.formdef.reverse_relations:
            from wcs.formdef_jobs import UpdateRelationsAfterJob

            job = UpdateRelationsAfterJob(carddata=self)
            job._update_key = (self._formdef.id, self.id)
            # do not register/run job if an identical job is already planned
            if job._update_key not in (
                getattr(x, '_update_key', None)
                for x in get_publisher().after_jobs or []
                if not x.completion_time
            ):
                job.store()
                get_publisher().add_after_job(job, force_async=True)
        self._has_changed_digest = False


class ApplicationCardData:
    def __init__(self, carddef):
        self.carddef = carddef
        self.slug = carddef.slug
        self.name = _('Data of "%s"') % carddef.name

    @classmethod
    def select(cls):
        from wcs.carddef import CardDef

        return [cls(x) for x in CardDef.select()]

    @classmethod
    def get_by_slug(cls, slug, **kwargs):
        from wcs.carddef import CardDef

        return cls(CardDef.get_by_slug(slug))

    def get_admin_url(self):
        return self.carddef.get_url()

    def get_dependencies(self):
        yield self.carddef

    def export_for_application(self):
        from wcs.backoffice.management import JsonFileExportAfterJob

        job = JsonFileExportAfterJob(self.carddef)
        items = self.carddef.data_class().select([StrictNotEqual('status', 'draft'), Null('anonymised')])
        job.create_export(self.carddef, fields=None, items=items, total_count=0)
        return job.result_file.get_content(), 'application/json'

    def import_from_file(self, content):
        from wcs.backoffice.data_management import ImportFromJsonAfterJob

        job = ImportFromJsonAfterJob(
            self.carddef, json.loads(content), update_mode='update', delete_mode='keep', user_id=None
        )
        job.id = job.DO_NOT_STORE
        job.execute()
