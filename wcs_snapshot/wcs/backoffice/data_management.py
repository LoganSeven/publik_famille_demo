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

import copy
import csv
import datetime
import io
import json
import uuid

from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_response, redirect
from quixote.html import htmltext

from wcs import fields
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql_criterias import NotContains, Null, StrictNotEqual
from wcs.workflows import ContentSnapshotPart

from ..qommon import _, errors, ngettext, template
from ..qommon.afterjobs import AfterJob
from ..qommon.form import FileWidget, Form, RadiobuttonsWidget
from .management import FormBackOfficeStatusPage, FormPage, ManagementDirectory
from .submission import FormFillPage


def get_import_csv_fields(carddef):
    class UserField:
        key = 'user'
        id = '_user'
        label = _('User (email or UUID)')

        def convert_value_from_str(self, x):
            return x

        def set_value(self, data, value):
            data['_user'] = value

    # skip non-data fields
    csv_fields = []
    for field in carddef.iter_fields(include_block_fields=True, with_backoffice_fields=False):
        if not isinstance(field, fields.WidgetField):
            continue
        if field.key == 'block' and field.get_max_items() == 1:
            # ignore BlockField if only one item
            continue
        block_field = getattr(field, 'block_field', None)
        if block_field:
            if block_field.get_max_items() > 1:
                # ignore fields of BlockField if more than one item
                continue
            # complete field label
            field.label = '%s - %s' % (block_field.label, field.label)
        csv_fields.append(field)
    if carddef.user_support == 'optional':
        return [UserField()] + csv_fields
    return csv_fields


class DataManagementDirectory(ManagementDirectory):
    do_not_call_in_templates = True
    _q_exports = ['']
    section = 'data'

    def add_breadcrumb(self):
        get_response().breadcrumb.append(('data/', _('Cards')))

    def is_accessible(self, user, traversal=False):
        if traversal:
            return super().is_accessible(user=user, traversal=traversal)
        if not user.can_go_in_backoffice():
            return False
        if get_publisher().get_backoffice_root().is_global_accessible('cards') and CardDef.keys():
            # open for admins as soon as there are cards
            return True
        # only include data management if there are accessible cards
        for carddef in CardDef.select(ignore_errors=True, lightweight=True, iterator=True):
            for role_id in user.get_roles():
                if role_id in (carddef.backoffice_submission_roles or []):
                    return True
                if role_id in (carddef.workflow_roles or {}).values():
                    return True
        # or if there is a card category where user is allowed to export data
        for category in CardDefCategory.select(ignore_errors=True):
            category_export_role_ids = [x.id for x in category.export_roles or []]
            for role_id in user.get_roles():
                if role_id in category_export_role_ids:
                    return True
        return False

    def get_carddefs(self):
        user = get_request().user
        if not user:
            return
        carddefs = CardDef.select(order_by='name', ignore_errors=True, lightweight=True)
        carddefs = [c for c in carddefs if user.is_admin or c.is_of_concern_for_user(user)]
        cats = CardDefCategory.select()
        CardDefCategory.sort_by_position(cats)
        for c in cats + [None]:
            for carddef in carddefs:
                if c is None and not carddef.category_id:
                    yield carddef
                if c is not None and carddef.category_id == c.id:
                    yield carddef

    def _q_index(self):
        get_response().set_title(_('Cards'))
        if not (CardDef.exists()):
            return self.empty_site_message(_('Cards'))
        if not self.is_accessible(get_request().user, traversal=False):
            raise errors.AccessForbiddenError()
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/data-management.html'], context={'view': self}
        )

    def _q_lookup(self, component):
        return CardPage(component)


class CardPage(FormPage):
    _q_exports = [
        '',
        'csv',
        'ods',
        'json',
        'export',
        'map',
        'geojson',
        'add',
        'actions',
        ('export-spreadsheet', 'export_spreadsheet'),
        ('save-view', 'save_view'),
        ('delete-view', 'delete_view'),
        ('import-file', 'import_file'),
        ('filter-options', 'filter_options'),
        ('data-sample-csv', 'data_sample_csv'),
        ('view-settings', 'view_settings'),
    ]
    admin_permission = 'cards'
    formdef_class = CardDef
    export_data_label = _('Export Data')
    search_label = _('Search in card content')
    formdef_view_label = _('View Card')
    has_json_export_support = True
    ensure_parent_category_in_url = False

    @property
    def add(self):
        return CardFillPage(self.formdef.url_name)

    def listing_top_actions(self):
        if not self.formdef.has_creation_permission(get_request().user):
            return ''
        return htmltext('<span class="actions"><a href="./add/">%s</a></span>') % _('Add')

    def get_default_filters(self, mode):
        if self.view:
            return self.view.get_default_filters()
        return ()

    def get_default_columns(self):
        if self.view:
            field_ids = self.view.get_columns()
        else:
            field_ids = ['id', 'time']
            for field in self.formdef.get_all_fields():
                if hasattr(field, 'get_view_value') and field.include_in_listing:
                    field_ids.append(field.id)
        return field_ids

    def get_filter_from_query(self, default=Ellipsis):
        return super().get_filter_from_query(default='all' if default is Ellipsis else default)

    def get_formdata_sidebar_actions(self, qs=''):
        r = super().get_formdata_sidebar_actions(qs=qs)
        if self.formdef.has_creation_permission(get_request().user):
            r += htmltext(
                '<li><a rel="popup" data-selector=".card-import-popup-content" href="import-file">%s</a></li>'
            ) % _('Import data from a file')
        return r

    def data_sample_csv(self):
        carddef_fields = get_import_csv_fields(self.formdef)
        output = io.StringIO()
        if len(carddef_fields) == 1:
            csv_output = csv.writer(output, quoting=csv.QUOTE_NONE, delimiter='\ue000', escapechar='\ue001')
        else:
            csv_output = csv.writer(output, quoting=csv.QUOTE_ALL)

        csv_output.writerow([f.label for f in carddef_fields])
        sample_line = []
        for f in carddef_fields:
            if f.convert_value_from_str is None:
                value = _('will be ignored - type %s not supported') % f.get_type_label()
            elif isinstance(f, fields.DateField):
                value = datetime.date.today()
            elif isinstance(f, fields.BoolField):
                value = _('Yes')
            elif isinstance(f, fields.EmailField):
                value = 'foo@example.com'
            elif isinstance(f, fields.MapField):
                value = '%(lat)s;%(lon)s' % get_publisher().get_default_position()
            elif isinstance(f, fields.ItemsField):
                value = 'id1|id2|...'
            else:
                value = 'value'
            sample_line.append(value)
        csv_output.writerow(sample_line)
        response = get_response()
        response.set_content_type('text/plain')
        response.set_header(
            'content-disposition', 'attachment; filename=%s-sample.csv' % self.formdef.url_name
        )
        return output.getvalue()

    def import_file(self):
        if not self.formdef.has_creation_permission(get_request().user):
            raise errors.AccessForbiddenError()
        context = {'required_fields': []}

        form = Form(enctype='multipart/form-data', use_tokens=False)
        form.add(FileWidget, 'file', title=_('File'), required=True)

        match_hint = (
            _('Cards will be matched using their unique identifier ("uuid" property).')
            if not self.formdef.id_template
            else _('Cards will be matched using their custom identifier ("id" property). ')
        )

        form.add(
            RadiobuttonsWidget,
            'update_mode',
            title=(
                _('Update mode (only for JSON imports)') if not self.formdef.id_template else _('Update mode')
            ),
            hint=_('Behaviour for existing cards that are found in the file.') + ' ' + match_hint,
            options=[
                ('update', _('Update'), 'update'),
                ('skip', _('Skip'), 'skip'),
            ],
            extra_css_class='widget-inline-radio',
            value='update',
        )
        form.add(
            RadiobuttonsWidget,
            'delete_mode',
            title=(
                _('Delete mode (only for JSON imports)') if not self.formdef.id_template else _('Delete mode')
            ),
            hint=_('Behaviour for existing cards that are not found in the file.') + ' ' + match_hint,
            options=[('keep', _('Keep'), 'keep'), ('delete', _('Delete'), 'delete')],
            extra_css_class='widget-inline-radio',
            value='keep',
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            file_content = form.get_widget('file').parse().fp.read()
            update_mode = form.get_widget('update_mode').parse()
            delete_mode = form.get_widget('delete_mode').parse()
            try:
                json_content = json.loads(file_content)
            except ValueError:
                # not json -> CSV
                try:
                    return self.import_csv_submit(
                        file_content,
                        update_mode=update_mode,
                        delete_mode=delete_mode,
                        submission_agent_id=get_request().user.id,
                    )
                except ValueError as e:
                    form.set_error('file', e)
            else:
                try:
                    return self.import_json_submit(
                        json_content,
                        update_mode=update_mode,
                        delete_mode=delete_mode,
                    )
                except ValueError as e:
                    form.set_error('file', e)

        get_response().breadcrumb.append(('import-file', _('Import File')))
        get_response().set_title(_('Import File'))
        context['html_form'] = form
        context['impossible_csv_fields'] = self.get_csv_impossible_fields()

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/card-data-import-form.html'], context=context
        )

    def get_csv_impossible_fields(self):
        impossible_fields = []
        for field in get_import_csv_fields(self.formdef):
            if not hasattr(field, 'required'):
                continue
            if field.is_required() and field.convert_value_from_str is None:
                impossible_fields.append(field.label)
        return impossible_fields

    def import_csv_submit(
        self,
        content,
        afterjob=True,
        api=False,
        update_mode='skip',
        delete_mode='keep',
        submission_agent_id=None,
    ):
        if b'\0' in content:
            raise ValueError(_('Invalid file format.'))

        impossible_fields = self.get_csv_impossible_fields()
        if impossible_fields:
            error = ngettext(
                '%s is required but cannot be filled from CSV.',
                '%s are required but cannot be filled from CSV.',
                len(impossible_fields),
            ) % ', '.join(impossible_fields)
            raise ValueError(error)

        for charset in ('utf-8', 'iso-8859-15'):
            try:
                content = content.decode(charset)
                break
            except UnicodeDecodeError:
                continue

        try:
            dialect = csv.Sniffer().sniff(content)
        except csv.Error:
            dialect = None

        reader = csv.reader(content.splitlines(keepends=True), dialect=dialect)
        try:
            caption = next(reader)
        except StopIteration:
            raise ValueError(_('Invalid CSV file.'))

        carddef_fields = get_import_csv_fields(self.formdef)
        if len(caption) < len(carddef_fields):
            raise ValueError(_('CSV file contains less columns than card fields.'))

        data_lines = []
        incomplete_lines = []
        for line_no, csv_line in enumerate(reader):
            if len(csv_line) != len(carddef_fields):
                # +2 because header and counting from 1.
                incomplete_lines.append(str(line_no + 2))
                continue
            data_lines.append(csv_line)

        if incomplete_lines:
            error_message = _('CSV file contains lines with wrong number of columns.')
            if len(incomplete_lines) < 5:
                error_message += ' ' + _('(line numbers %s)') % ', '.join(incomplete_lines)
            else:
                error_message += ' ' + _('(line numbers %s and more)') % ', '.join(incomplete_lines[:5])
            raise ValueError(error_message)

        job = ImportFromCsvAfterJob(
            carddef=self.formdef,
            data_lines=data_lines,
            update_mode=update_mode,
            delete_mode=delete_mode,
            submission_agent_id=submission_agent_id,
        )
        if afterjob:
            get_publisher().add_after_job(job)
            if api:
                return job
            job.store()
            return redirect(job.get_processing_url())

        job.id = job.DO_NOT_STORE
        job.execute()

    def import_json_submit(
        self, json_content, *, update_mode='skip', delete_mode='keep', afterjob=True, api=False
    ):
        # basic check, looks like valid json card content?
        if not isinstance(json_content, dict) or 'data' not in json_content:
            raise ValueError(_('Invalid JSON file.'))

        job = ImportFromJsonAfterJob(
            carddef=self.formdef,
            json_content=json_content,
            update_mode=update_mode,
            delete_mode=delete_mode,
            user_id=get_request().user.id,
        )
        if afterjob:
            get_publisher().add_after_job(job)
            if api:
                return job
            job.store()
            return redirect(job.get_processing_url())

        job.id = job.DO_NOT_STORE
        job.execute()

    def _q_lookup(self, component):
        view_lookup_response = self._q_lookup_view(component)
        if view_lookup_response is not None:
            return view_lookup_response

        try:
            filled = self.formdef.data_class().get_by_id(component)
        except KeyError:
            raise errors.TraversalError()
        return CardBackOfficeStatusPage(self.formdef, filled, parent_view=self)


class CardFillPage(FormFillPage):
    formdef_class = CardDef
    has_channel_support = False
    has_user_support = False
    already_submitted_message = _('This card has already been submitted.')
    required_user_message = _('The card must be associated to an user.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.formdef.user_support == 'optional':
            self.has_user_support = True

    def get_default_return_url(self):
        if self.formdef.is_of_concern_for_user(get_request().user):
            return '%s/data/%s/' % (get_publisher().get_backoffice_url(), self.formdef.url_name)
        # redirect to cards index page if the user is not allowed to see the cards
        return '%s/data/' % get_publisher().get_backoffice_url()

    def redirect_after_submitted(self, url, filled):
        if get_request().form.get('_popup'):
            popup_response_data = json.dumps(
                {
                    'value': str(filled.get_natural_key()),
                    'obj': str(filled.default_digest),
                    'edit_related_url': filled.get_edit_related_url() or '',
                    'view_related_url': filled.get_view_related_url() or '',
                }
            )
            return template.QommonTemplateResponse(
                templates=['wcs/backoffice/popup_response.html'],
                context={'popup_response_data': popup_response_data},
                is_django_native=True,
            )
        return super().redirect_after_submitted(url, filled)

    def create_form(self, *args, **kwargs):
        form = super().create_form(*args, **kwargs)
        if get_request().form.get('_popup'):
            form.add_hidden('_popup', 1)
        return form


class CardBackOfficeStatusPage(FormBackOfficeStatusPage):
    form_page_class = CardFillPage

    sidebar_recorded_message = _('The card has been recorded on %(date)s with the identifier %(identifier)s.')
    sidebar_recorded_by_agent_message = _(
        'The card has been recorded on %(date)s with the identifier %(identifier)s by %(agent)s.'
    )
    replay_detailed_message = _(
        'Another action has been performed on this card in the meantime and data may have been changed.'
    )

    def should_fold_summary(self, mine, request_user):
        return False

    def get_bottom_links(self):
        r = super().get_bottom_links()
        if (
            self.filled.submission_agent_id == str(get_request().user.id)
            and self.filled.formdef.has_creation_permission(get_request().user)
            and self.filled.receipt_time > (localtime() - datetime.timedelta(minutes=5))
        ):
            r += htmltext(' - <a href="../add/">%s</a>') % _('Add another card')
        return r


class ImportFromCsvAfterJob(AfterJob):
    def __init__(self, carddef, data_lines, update_mode, delete_mode, submission_agent_id):
        super().__init__(
            label=_('Importing data into cards'),
            carddef_class=carddef.__class__,
            carddef_id=carddef.id,
            data_lines=data_lines,
            update_mode=update_mode,
            delete_mode=delete_mode,
            submission_agent_id=submission_agent_id,
        )

    def user_lookup(self, user_value):
        if self.carddef.user_support != 'optional':
            return None
        return get_publisher().user_class.lookup_by_string(user_value)

    def execute(self):
        self.carddef = self.kwargs['carddef_class'].get(self.kwargs['carddef_id'])
        update_mode = self.kwargs['update_mode']
        delete_mode = self.kwargs['delete_mode']
        carddata_class = self.carddef.data_class()
        self.submission_agent_id = self.kwargs['submission_agent_id']
        self.total_count = len(self.kwargs['data_lines'])
        self.store()

        carddef_fields = get_import_csv_fields(self.carddef)
        seen_ids = set()

        for csv_line in self.kwargs['data_lines']:
            data_instance = carddata_class()
            data_instance.data = {}
            block_data = {}

            data_field_ids = set()

            for i, field in enumerate(carddef_fields):
                block_field = getattr(field, 'block_field', None)
                value = csv_line[i].strip()
                # skip empty values
                if not value:
                    if not block_field:
                        data_field_ids.add(field.id)
                    continue
                # skip unsupported field types
                if field.convert_value_from_str is None:
                    continue
                if not block_field:
                    field.set_value(data_instance.data, field.convert_value_from_str(value))
                    data_field_ids.add(field.id)
                    continue

                # field in a BlockField
                if not block_data.get(block_field.id):
                    block_data[block_field.id] = {'data': [{}], 'schema': {}, 'block_field': block_field}
                field.set_value(block_data[block_field.id]['data'][0], field.convert_value_from_str(value))
                block_data[block_field.id]['schema'][field.id] = field.key

            # fill BlockFields
            for data in block_data.values():
                block_field = data.pop('block_field')
                block_field.set_value(data_instance.data, data)

            user_value = data_instance.data.pop('_user', None)
            data_instance.user = self.user_lookup(user_value)
            data_instance.submission_context = {
                'method': 'csv_import',
                'job_id': self.id,
            }
            data_instance.submission_agent_id = self.submission_agent_id
            data_instance.submission_channel = 'file-import'

            new_card = True
            if self.carddef.id_template:
                # check id is unique
                old_digests = data_instance.digests
                data_instance.set_auto_fields()
                data_instance.digests = old_digests
                seen_ids.add(data_instance.id_display)
                try:
                    carddata_with_same_id = self.carddef.data_class().get_by_id(data_instance.id_display)
                except KeyError:
                    pass  # unique id, fine
                else:
                    if update_mode == 'skip':
                        self.increment_count()
                        continue
                    # overwrite (only fields from CSV columns, not unsupported or backoffice fields)
                    new_card = False
                    orig_data = copy.copy(carddata_with_same_id.data)
                    for data_field_id in data_field_ids:
                        for key in (
                            str(data_field_id),
                            f'{data_field_id}_display',
                            f'{data_field_id}_structured',
                        ):
                            carddata_with_same_id.data[key] = data_instance.data.get(key)
                    ContentSnapshotPart.take(
                        formdata=carddata_with_same_id, old_data=orig_data, user=self.submission_agent_id
                    )
                    carddata_with_same_id.record_workflow_event('csv-import-updated')
                    carddata_with_same_id.store()

            if new_card:
                data_instance.store()
                data_instance.refresh_from_storage()
                data_instance.just_created()
                data_instance.store()

                get_publisher().reset_formdata_state()
                get_publisher().substitutions.feed(data_instance)

                data_instance.record_workflow_event('csv-import-created')
                data_instance.perform_workflow()

            self.increment_count()

        if self.carddef.id_template and delete_mode == 'delete':
            for carddata_id in self.carddef.data_class().keys(
                [StrictNotEqual('status', 'draft'), Null('anonymised'), NotContains('id_display', seen_ids)]
            ):
                self.carddef.data_class().remove_object(carddata_id)

    def done_action_url(self):
        carddef = self.kwargs['carddef_class'].get(self.kwargs['carddef_id'])
        return carddef.get_url()

    def done_action_label(self):
        return _('Back to Listing')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}


class ImportFromJsonAfterJob(AfterJob):
    def __init__(self, carddef, json_content, update_mode, delete_mode, user_id):
        json_content_file = PicklableUpload('import.json', 'application/json')
        json_content_file.receive([json.dumps(json_content).encode()])
        super().__init__(
            label=_('Importing data into cards'),
            carddef_class=carddef.__class__,
            carddef_id=carddef.id,
            json_content_file=json_content_file,
            update_mode=update_mode,
            delete_mode=delete_mode,
            user_id=user_id,
        )

    @property
    def carddef(self):
        return self.kwargs['carddef_class'].get(self.kwargs['carddef_id'])

    def execute(self):
        json_content = json.loads(self.kwargs['json_content_file'].get_content())
        user_id = self.kwargs.get('user_id')
        update_mode = self.kwargs['update_mode']
        delete_mode = self.kwargs['delete_mode']
        from wcs.api import posted_json_data_to_formdata_data

        seen_ids = set()

        for json_data in json_content['data']:
            if 'fields' not in json_data:
                self.mark_as_failed(_('Invalid JSON file (missing "fields" key on entry).'))
                raise ValueError(self.failure_label)
            json_data = copy.deepcopy(json_data)
            carddata = self.carddef.data_class()()
            carddata.data = {}

            existing_carddata = None
            normalized_uuid = None
            try:
                if self.carddef.id_template and json_data.get('id'):
                    existing_carddata = self.carddef.data_class().get_by_id(json_data.get('id'))
                elif json_data.get('uuid'):
                    try:
                        normalized_uuid = str(uuid.UUID(json_data.get('uuid')))
                    except ValueError:
                        raise KeyError()  # ignore invalid uuid
                    existing_carddata = self.carddef.data_class().get_by_uuid(normalized_uuid)
            except KeyError:
                pass

            if existing_carddata:
                if update_mode == 'skip':
                    self.increment_count()
                    continue
                carddata = existing_carddata
                orig_data = copy.copy(existing_carddata.data)
            elif normalized_uuid:
                # create with provided uuid
                carddata.uuid = normalized_uuid

            # load fields
            carddata.data.update(posted_json_data_to_formdata_data(self.carddef, json_data['fields']))

            # load backoffice fields if any
            if 'fields' in (json_data.get('workflow') or {}):
                backoffice_data_dict = posted_json_data_to_formdata_data(
                    self.carddef, json_data['workflow']['fields']
                )
                carddata.data.update(backoffice_data_dict)

            # set user if any
            if 'user' in json_data:
                carddata.set_user_from_json(json_data['user'])

            try:
                card_status = json_data['workflow'].get('real_status') or json_data['workflow'].get('status')
                # check it has a valid 'id' key
                card_status_id = self.carddef.workflow.get_status(card_status['id']).id
            except KeyError:
                card_status = None
                card_status_id = None

            if self.carddef.id_template:
                # check id is unique
                old_digests = carddata.digests
                carddata.set_auto_fields()
                carddata.digests = old_digests
                seen_ids.add(carddata.id_display)
                try:
                    carddata_with_same_id = self.carddef.data_class().get_by_id(carddata.id_display)
                except KeyError:
                    pass  # unique id, fine
                else:
                    if update_mode == 'update' and carddata.id == carddata_with_same_id.id:  # fine
                        orig_data = copy.copy(carddata.data)
                    elif update_mode == 'update':  # overwrite
                        orig_data = copy.copy(carddata_with_same_id.data)
                        carddata_with_same_id.data = carddata.data
                        carddata = carddata_with_same_id
                    else:
                        # not asked to update, and we do not want to create a duplicate, so skip silently
                        self.increment_count()
                        continue
            else:
                seen_ids.add(carddata.uuid)

            if carddata.id is None:
                # no id, this is a new card
                carddata.submission_context = {
                    'method': 'json_import',
                    'job_id': self.id,
                }
                carddata.store()
                carddata.refresh_from_storage()
                carddata.just_created()

                if not card_status_id:
                    # perform as new
                    carddata.store()

                    get_publisher().reset_formdata_state()
                    get_publisher().substitutions.feed(carddata)
                    carddata.record_workflow_event('json-import-created')
                    carddata.perform_workflow()
                else:
                    # set to status specified in json
                    carddata.status = f'wf-{card_status_id}'
                    carddata.evolution[-1].status = carddata.status
                    carddata.store()
                    carddata.record_workflow_event('json-import-created')
            else:
                # update data of existing card
                ContentSnapshotPart.take(formdata=carddata, old_data=orig_data, user=user_id)
                carddata.record_workflow_event('json-import-updated')
                carddata.store()
                if card_status and carddata.status != f'wf-{card_status_id}':
                    # switch status (but do not execute)
                    carddata.jump_status(card_status_id)

            self.increment_count()

        if delete_mode == 'delete':
            criterias = [StrictNotEqual('status', 'draft'), Null('anonymised')]
            if self.carddef.id_template:
                criterias.append(NotContains('id_display', seen_ids))
            else:
                criterias.append(NotContains('uuid', seen_ids))
            for carddata_id in self.carddef.data_class().keys(criterias):
                self.carddef.data_class().remove_object(carddata_id)

    def done_action_url(self):
        return self.carddef.get_url()

    def done_action_label(self):
        return _('Back to Listing')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}
