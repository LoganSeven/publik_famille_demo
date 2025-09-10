# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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

import contextlib
import datetime
import glob
import itertools
import os

from quixote import get_publisher, get_session

from wcs.qommon import _
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.cron import CronJob
from wcs.qommon.misc import is_attachment, is_upload
from wcs.qommon.publisher import get_publisher_class
from wcs.sql_criterias import Contains, Equal, Null, Or, get_field_id


def clean_drafts(publisher, **kwargs):
    import wcs.qommon.storage as st
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef

    job = kwargs.pop('job', None)
    for formdef in FormDef.select() + CardDef.select():
        with (
            job.log_long_job('%s %s' % (formdef.xml_root_node, formdef.url_name))
            if job
            else contextlib.ExitStack()
        ):
            removal_date = datetime.date.today() - datetime.timedelta(days=formdef.get_drafts_lifespan())
            for formdata in formdef.data_class().select(
                [st.Equal('status', 'draft'), st.Less('receipt_time', removal_date.timetuple())]
            ):
                formdata.remove_self()


def clean_unused_files(publisher, **kwargs):
    unused_files_behaviour = publisher.get_site_option('unused-files-behaviour')
    if unused_files_behaviour not in ('move', 'remove'):
        return

    known_filenames = set()
    known_filenames.update([x for x in glob.glob(os.path.join(publisher.app_dir, 'uploads/*'))])
    known_filenames.update([x for x in glob.glob(os.path.join(publisher.app_dir, 'attachments/*/*'))])

    def accumulate_filenames():
        from wcs.applications import Application
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        for formdef in FormDef.select(ignore_migration=True) + CardDef.select(ignore_migration=True):
            for option_data in (formdef.workflow_options or {}).values():
                if is_upload(option_data):
                    yield option_data.get_fs_filename()
            for formdata in formdef.data_class().select_iterator(ignore_errors=True, itersize=200):
                for field_data in formdata.get_all_file_data(with_history=True):
                    if is_upload(field_data):
                        yield field_data.get_fs_filename()
                    elif is_attachment(field_data):
                        yield field_data.filename
        for user in publisher.user_class.select():
            for field_data in (user.form_data or {}).values():
                if is_upload(field_data):
                    yield field_data.get_fs_filename()

        for application in Application.select():
            if is_upload(application.icon):
                yield application.icon.get_fs_filename()

        for job in AfterJob.select():
            for attribute in ('result_file', 'tar_content_file', 'json_content_file'):
                if is_upload(getattr(job, attribute, None)):
                    yield getattr(job, attribute).get_fs_filename()

    used_filenames = set()
    for filename in accumulate_filenames():
        if not filename:  # alternative storage
            continue
        if not os.path.isabs(filename):
            filename = os.path.join(publisher.app_dir, filename)
        used_filenames.add(filename)

    unused_filenames = known_filenames - used_filenames
    for filename in unused_filenames:
        try:
            if unused_files_behaviour == 'move':
                new_filename = os.path.join(
                    publisher.app_dir, 'unused-files', filename[len(publisher.app_dir) + 1 :]
                )
                if os.path.exists(new_filename):
                    os.unlink(filename)
                else:
                    new_dirname = os.path.dirname(new_filename)
                    if not os.path.exists(new_dirname):
                        os.makedirs(new_dirname)
                    os.rename(filename, new_filename)
            else:
                os.unlink(filename)
        except OSError:
            pass


def update_storage_all_formdefs(publisher, **kwargs):
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef

    for formdef in itertools.chain(FormDef.select(), CardDef.select()):
        formdef.update_storage()
        if formdef.sql_integrity_errors:
            # print errors, this will get them in the cron output, that hopefully
            # a sysadmin will read.
            print(f'! Integrity errors in {formdef.get_admin_url()}')


def register_cronjobs():
    # every night:
    # * update storage of all formdefs
    get_publisher_class().register_cronjob(
        CronJob(update_storage_all_formdefs, name='update_storage', hours=[2], minutes=[0])
    )
    # * and look for:
    #   * expired drafts
    get_publisher_class().register_cronjob(CronJob(clean_drafts, name='clean_drafts', hours=[2], minutes=[0]))
    #   * and unused files
    get_publisher_class().register_cronjob(
        CronJob(clean_unused_files, name='clean_unused_files', hours=[2], minutes=[0])
    )


class UpdateDigestAfterJob(AfterJob):
    label = _('Updating digests')

    def __init__(self, formdefs):
        super().__init__(formdefs=[(x.__class__, x.id) for x in formdefs if x.id])

    def do_formdata_action(self, formdata):
        # update digests
        if formdata.set_digests_field():
            formdata.update_column('digests')
            self.updated_ids.add(formdata.id)

    def execute(self):
        for formdef_class, formdef_id in self.kwargs['formdefs']:
            self.updated_ids = set()
            formdef = formdef_class.get(formdef_id)
            for formdata in formdef.data_class().select_iterator(order_by='id', itersize=200):
                self.do_formdata_action(formdata)

            # then update relations
            from wcs.carddef import CardDef

            if self.updated_ids and isinstance(formdef, CardDef) and formdef.reverse_relations:
                for formdata in formdef.data_class().select_iterator(
                    [Contains('id', self.updated_ids)], order_by='id', itersize=200
                ):
                    formdata.update_related()


class UpdateStatisticsDataAfterJob(UpdateDigestAfterJob):
    label = _('Updating statistics data')

    @classmethod
    def do_formdata_action(cls, formdata):
        if formdata.set_statistics_data_field():
            formdata.update_column('statistics_data')


class UpdateDigestsAndStatisticsDataAfterJob(UpdateDigestAfterJob):
    label = _('Updating digests and statistics data')

    def do_formdata_action(self, formdata):
        super().do_formdata_action(formdata)
        UpdateStatisticsDataAfterJob.do_formdata_action(formdata)


class UpdateRelationsAfterJob(AfterJob):
    label = _('Updating relations')

    def __init__(self, carddata):
        super().__init__(carddef_id=carddata.formdef.id, carddata_id=carddata.id)

    def execute(self):
        from .carddef import CardDef
        from .formdef import FormDef

        if getattr(get_publisher(), '_update_related_seen', None) is None:
            get_publisher()._update_related_seen = set()

        # keep track of objects that have been updated, to avoid cycles
        update_related_seen = get_publisher()._update_related_seen

        try:
            carddef = CardDef.cached_get(self.kwargs['carddef_id'])
            carddata = carddef.data_class().get(self.kwargs['carddata_id'])
        except KeyError:
            # card got removed (probably the afterjob met some unexpected delay), ignore.
            return

        klass = {'carddef': CardDef, 'formdef': FormDef}
        publisher = get_publisher()

        # check all known reverse relations
        for obj_ref in {x['obj'] for x in carddef.reverse_relations}:
            obj_type, obj_slug = obj_ref.split(':')
            obj_class = klass.get(obj_type)
            try:
                objdef = obj_class.get_by_slug(obj_slug, use_cache=True)
            except KeyError:
                continue
            criterias = []
            fields = []

            # get fields referencing the card model (only item and items fields, as string
            # field with data source is just for completion, and computed field with data
            # source, do not store a display value.
            for field in objdef.iter_fields(include_block_fields=True):
                if field.key not in ('item', 'items'):
                    continue
                data_source = getattr(field, 'data_source', None)
                if not data_source:
                    continue
                data_source_type = data_source.get('type')
                if (
                    not data_source_type.startswith('carddef:')
                    or data_source_type.split(':')[1] != carddef.slug
                ):
                    continue
                fields.append(field)
                criterias.append(Equal(get_field_id(field), carddata.identifier, field=field))
            if not criterias:
                continue

            def update_data(field, data):
                display_value = data.get(f'{field.id}_display')
                field.set_value(data, data.get(field.id))
                return bool(data.get(f'{field.id}_display') != display_value)

            # look for all formdata, including drafts, excluding anonymised
            select_criterias = [Null('anonymised'), Or(criterias)]
            for objdata in objdef.data_class().select_iterator(clause=select_criterias, itersize=200):
                objdata_seen_key = f'{objdata.formdef.xml_root_node}:{objdata.formdef.slug}:{objdata.id}'
                if objdata_seen_key in update_related_seen:
                    # do not allow updates to cycle back
                    continue

                publisher.reset_formdata_state()
                publisher.substitutions.feed(objdata.formdef)
                publisher.substitutions.feed(objdata)

                objdata_changed = False
                for field in fields:
                    if getattr(field, 'block_field', None):
                        if objdata.data.get(field.block_field.id):
                            blockdata_changed = False
                            for block_row_data in objdata.data[field.block_field.id]['data']:
                                blockdata_changed |= update_data(field, block_row_data)
                            if blockdata_changed:
                                # if block data changed, maybe block digest changed too
                                update_data(field.block_field, objdata.data)
                            objdata_changed |= blockdata_changed
                    else:
                        objdata_changed |= update_data(field, objdata.data)
                if objdata_changed:
                    update_related_seen.add(objdata_seen_key)
                    objdata.store()


class PerformWorkflowJob(AfterJob):
    url = None

    def __init__(self, formdata, **kwargs):
        formdef = formdata._formdef
        super().__init__(
            formdef_class=formdef.__class__,
            formdef_id=formdef.id,
            formdata_id=formdata.id,
            session_user_id=get_session().get_user_id() if get_session() else None,
            **kwargs,
        )

    def execute(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        formdata = formdef.data_class().get(self.kwargs['formdata_id'])
        if self.kwargs['session_user_id']:
            user = get_publisher().user_class.get(self.kwargs['session_user_id'], ignore_errors=True)
        with get_publisher().substitutions.freeze():
            get_publisher().substitutions.feed(user)
            try:
                self.url = formdata.perform_workflow(check_progress=False)
                self.store()
            finally:
                formdata.workflow_processing_afterjob_id = None
                formdata.store()
