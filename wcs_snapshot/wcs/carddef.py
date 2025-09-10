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

import io
import sys
from subprocess import PIPE, Popen

from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher

from wcs.formdef_base import FormDefBase, FormDefDoesNotExist, get_formdefs_of_all_kinds
from wcs.qommon import _, misc, pgettext_lazy
from wcs.qommon import storage as st
from wcs.qommon.storage import StorableObject
from wcs.sql import SqlCardDef
from wcs.sql_criterias import ElementEqual, ElementILike, Equal, Null, StrictNotEqual


class CardDefDoesNotExist(FormDefDoesNotExist):
    error_message = _('No such card model: %s')


class CardDef(FormDefBase, SqlCardDef):
    _names = 'carddefs'
    backoffice_class = 'wcs.backoffice.cards.CardDefPage'
    backoffice_section = 'cards'
    data_sql_prefix = 'carddata'
    pickle_module_name = 'carddef'
    xml_root_node = 'carddef'
    verbose_name = _('Card model')
    verbose_name_plural = _('Card models')
    item_name = pgettext_lazy('item', 'card')
    item_name_plural = pgettext_lazy('item', 'cards')

    confirmation = False
    history_pane_default_mode = 'collapsed'
    submission_user_association = 'none'

    # users are not allowed to access carddata where they're submitter.
    user_allowed_to_access_own_data = False

    # won't appear in frontoffice
    may_appear_in_frontoffice = False

    submission_user_association_available_options = ['none', 'any', 'any-required']

    category_class = 'wcs.categories.CardDefCategory'

    def migrate(self):
        super().migrate()
        if self.__dict__.get('fields') is Ellipsis:
            # don't run migration on lightweight objects
            return

        changed = False
        if self.user_support and self.submission_user_association == 'none':  # 2024-04-27
            self.submission_user_association = 'any'
            changed = True

        if changed:
            self.store(comment=_('Automatic update'), snapshot_store_user=False)

    def data_class(self, mode=None):
        if 'carddef' not in sys.modules:
            sys.modules['carddef'] = sys.modules[__name__]
        if hasattr(sys.modules['carddef'], self.data_class_name):
            data_class = getattr(sys.modules['carddef'], self.data_class_name)
            # only use existing data class if it has a reference to this actual
            # carddef
            if data_class._formdef is self:
                return data_class
        from . import sql

        if self.use_test_data_class:
            table_name = sql.get_formdef_test_table_name(self)
        else:
            table_name = sql.get_formdef_table_name(self)

        cls = type(self.data_class_name, (sql.SqlCardData,), {'_formdef': self, '_table_name': table_name})
        setattr(sys.modules['carddef'], self.data_class_name, cls)
        setattr(sys.modules['wcs.carddef'], self.data_class_name, cls)

        return cls

    @classmethod
    def get_default_workflow(cls):
        from wcs.workflows import Workflow

        workflow = Workflow(name=force_str(_('Default (cards)')))
        workflow.id = '_carddef_default'
        workflow.slug = '_carddef_default'
        workflow.roles = {
            '_viewer': force_str(_('Viewer')),
            '_editor': force_str(_('Editor')),
        }
        status = workflow.add_status(force_str(_('Recorded')), 'recorded')
        deleted_status = workflow.add_status(force_str(_('Deleted')), 'deleted')

        editable = status.add_action('editable', id='_editable')
        editable.by = ['_editor']
        editable.label = force_str(_('Edit Card'))
        editable.status = status.id

        action_delete = status.add_action('choice', id='_action_delete')
        action_delete.by = ['_editor']
        action_delete.label = force_str(_('Delete Card'))
        action_delete.status = deleted_status.id
        action_delete.require_confirmation = True

        deleted_status.add_action('remove', id='_remove')

        return workflow

    def get_url(self, backoffice=False, **kwargs):
        # always return backoffice URL
        base_url = get_publisher().get_backoffice_url() + '/data'
        return '%s/%s/' % (base_url, self.url_name)

    def get_backoffice_submission_url(self):
        return self.get_url() + 'add/'

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        return '%s/cards/%s/' % (base_url, self.id)

    def get_api_url(self):
        base_url = get_publisher().get_frontoffice_url()
        return '%s/api/cards/%s/' % (base_url, self.url_name)

    def storage_store(self, comment=None, *args, **kwargs):
        self.roles = self.backoffice_submission_roles
        SqlCardDef.store(self, **kwargs)

    def update_category_reference(self):
        # only relevant for formdefs
        pass

    @classmethod
    def get_carddefs_as_data_source(cls):
        carddefs_by_id = {}
        for carddef in cls.select(ignore_errors=True, order_by='name'):
            if not carddef.default_digest_template:
                continue
            data_source_id = 'carddef:%s' % carddef.url_name
            carddefs_by_id[str(carddef.id)] = carddef
            yield (carddef, carddef.name, data_source_id, None)
            if carddef.user_support:
                data_source_id = 'carddef:%s:_with_user_filter' % carddef.url_name
                yield (carddef, _('%s (filtered on user)') % carddef.name, data_source_id, None)
        clauses = [Equal('formdef_type', 'carddef'), Equal('visibility', 'datasource')]
        for custom_view in get_publisher().custom_view_class.select(clauses):
            carddef = carddefs_by_id.get(custom_view.formdef_id)
            if not carddef:
                continue
            data_source_id = 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)
            yield (carddef, '%s - %s' % (carddef.name, custom_view.title), data_source_id, custom_view)

    @classmethod
    def get_data_source_custom_view(cls, data_source_id, carddef=None):
        parts = data_source_id.split(':')
        if len(parts) != 3:
            return None
        lookup_criterias = [
            Equal('formdef_type', 'carddef'),
            Equal('visibility', 'datasource'),
            Equal('slug', parts[2]),
        ]
        if carddef is not None:
            lookup_criterias.append(Equal('formdef_id', str(carddef.id)))
        for custom_view in get_publisher().custom_view_class.select(lookup_criterias):
            try:
                formdef = custom_view.formdef
            except KeyError:
                continue
            if formdef.url_name == parts[1]:
                return custom_view
        return None

    @classmethod
    def get_data_source_items(
        cls,
        data_source_id,
        query=None,
        limit=None,
        custom_view=None,
        *,
        get_by_id=None,
        get_by_ids=None,
        get_by_text=None,
        with_related_urls=False,
        with_files_urls=False,
        structured=False,
    ):
        # noqa pylint: disable=too-many-arguments
        assert data_source_id.startswith('carddef:')
        parts = data_source_id.split(':')
        try:
            carddef = cls.get_by_urlname(parts[1], use_cache=True)
        except KeyError:
            return []
        criterias = [StrictNotEqual('status', 'draft'), Null('anonymised')]
        order_by = None
        digest_key = 'default'
        if len(parts) > 2:
            if parts[2] == '_with_user_filter':
                if not get_by_id:
                    variables = get_publisher().substitutions.get_context_variables(mode='lazy')
                    try:
                        user = variables['form_user']
                    except KeyError:
                        user = None
                    if not user:
                        return []
                    criterias.append(Equal('user_id', str(user.id)))
            else:
                if custom_view is None:
                    custom_view = cls.get_data_source_custom_view(data_source_id, carddef=carddef)
                    if not custom_view:
                        return []
                order_by = carddef.get_order_by(custom_view.order_by)
                if not (get_by_id or get_by_ids):
                    criterias.extend(custom_view.get_criterias(formdef=carddef, compile_templates=True))

        if custom_view:
            view_digest_key = 'custom-view:%s' % custom_view.get_url_slug()
            if view_digest_key in (carddef.digest_templates or {}):
                digest_key = view_digest_key

        if get_publisher() and not get_publisher().is_using_default_language():
            digest_template = carddef.digest_templates.get(digest_key)
            if '|translate' in digest_template:
                digest_key += ':%s' % get_publisher().current_language

        if query:
            criterias.append(ElementILike('digests', digest_key, query))
        if get_by_id:
            try:
                criterias.append(carddef.get_by_id_criteria(get_by_id))
            except OverflowError:
                return []
        if get_by_ids is not None:
            try:
                criterias.append(carddef.get_by_multiple_id_criteria(get_by_ids))
            except ValueError:  # overflow
                return []
        if get_by_text is not None:
            if not get_by_text:
                # don't match empty digests
                return []
            criterias.append(ElementEqual('digests', digest_key, get_by_text))

        group_by = custom_view.group_by if custom_view else None
        if not (group_by or get_by_id or get_by_text or with_related_urls or with_files_urls or structured):
            items = carddef.data_class().select_as_items(
                digest_key, clause=criterias, order_by=order_by, limit=limit
            )
        else:
            items = [
                x.get_data_source_structured_item(
                    digest_key=digest_key,
                    group_by=group_by,
                    with_related_urls=with_related_urls,
                    with_files_urls=with_files_urls,
                )
                for x in carddef.data_class().select(clause=criterias, order_by=order_by, limit=limit)
            ]
        if order_by is None:
            items.sort(key=lambda x: misc.simplify(x['text']))
        if group_by:
            items.sort(key=lambda x: misc.simplify(x['group_by']))
        return items

    def is_used(self):
        for formdef in get_formdefs_of_all_kinds():
            if self.is_used_in_formdef(formdef):
                return True
        return False

    def is_used_in_formdef(self, formdef):
        for field in formdef.fields or []:
            data_source = getattr(field, 'data_source', None)
            if not (data_source and data_source.get('type')):
                continue
            data_source_id = 'carddef:%s' % self.url_name
            if data_source.get('type') == data_source_id:
                return True
            if data_source.get('type').startswith('%s:' % data_source_id):
                # custom view
                return True
        return False

    def usage_in_formdefs(self):
        for formdef in get_formdefs_of_all_kinds():
            if self.is_used_in_formdef(formdef):
                yield formdef

    @classmethod
    def get_data_source_referenced_varnames(cls, data_source_id, formdef):
        parts = data_source_id.split(':')
        if len(parts) != 3:
            return []
        try:
            carddef = cls.get_by_urlname(parts[1], use_cache=True)
        except KeyError:
            return []
        custom_view = cls.get_data_source_custom_view(data_source_id, carddef=carddef)
        if custom_view is None:
            return []
        varnames = []
        if 'filter-user-function-value' in custom_view.filters:
            varnames.append('__user__')
        for criteria in custom_view.get_criterias(formdef=carddef, keep_templates=True):
            varnames.extend(criteria.get_referenced_varnames(formdef))
        return varnames

    def has_image_field(self):
        for f in self.fields:
            if f.key == 'file' and f.varname == 'image':
                return True
        return False

    def get_default_management_sidebar_items(self):
        return {
            'general',
            'submission-context',
            'user',
            'geolocation',
            'custom-template',
        }

    def get_management_sidebar_available_items(self):
        excluded_parts = ['pending-forms']
        return [x for x in super().get_management_sidebar_available_items() if x[0] not in excluded_parts]


def get_cards_graph(category=None, show_orphans=False):
    out = io.StringIO()
    out.write('digraph main {\n')
    out.write('node [shape=box,style=filled];\n')
    out.write('edge [];\n')

    criterias = []
    if category is not None:
        criterias = [st.Equal('category_id', str(category.id))]
    carddefs = CardDef.select(clause=criterias)
    carddefs_slugs = [c.url_name for c in carddefs]

    def check_relations(carddef_ref, fields, check_blocks=True, prefix=''):
        cardinality = {
            'string': '1..n',
            'item': '1..n',
            'items': 'n..n',
            'computed': '1..n',
        }
        for field in fields:
            data_source = getattr(field, 'data_source', None)
            if data_source and data_source['type'].startswith('carddef:'):
                slug = field.data_source['type'].split(':')[1]
                if not show_orphans and slug not in carddefs_slugs:
                    # don't report extra category relations
                    continue
                label = '%s%s %s' % (prefix, field.varname or field.label, cardinality.get(field.key))
                yield '%s -> card_%s [label="%s"];' % (
                    carddef_ref,
                    slug.replace('-', '_'),
                    label,
                )
            if check_blocks and field.key == 'block':
                yield from check_relations(
                    carddef_ref,
                    field.block.fields,
                    check_blocks=False,
                    prefix='%s (block) ' % (field.varname or field.label),
                )

    records = []
    relations = []

    for carddef in carddefs:
        carddef_ref = 'card_%s' % carddef.url_name.replace('-', '_')
        record = '%s [shape=record,label="<card>%s",URL="%s"];' % (
            carddef_ref,
            carddef.name,
            carddef.get_admin_url(),
        )
        records.append(record)
        relations += list(check_relations(carddef_ref, carddef.get_all_fields()))
    if not show_orphans:
        for record in records[:]:
            if not [x for x in relations if record.split()[0] in x.split()]:
                records.remove(record)

    for record in records:
        out.write('%s\n' % record)

    for relation in relations:
        out.write('%s\n' % relation)

    out.write('}\n')

    out = out.getvalue()
    try:
        with Popen(['dot', '-Tsvg'], stdin=PIPE, stdout=PIPE) as process:
            out = process.communicate(force_bytes(out))[0]
            if process.returncode != 0:
                return ''
    except OSError:
        return ''

    return out


class FileCardDef(FormDefBase, StorableObject):
    # legacy class for migration
    _names = 'carddefs'
    _reset_class = False

    def storage_store(self, comment=None, *args, **kwargs):
        self.roles = self.backoffice_submission_roles
        StorableObject.store(self, **kwargs)
