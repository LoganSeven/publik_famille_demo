# w.c.s. - web application for online forms
# Copyright (C) 2005-2015  Entr'ouvert
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

from quixote import get_publisher, get_request, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.categories import DataSourceCategoriesDirectory, get_categories
from wcs.admin.documentable import DocumentableMixin
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, DataSourceCategory
from wcs.data_sources import (
    DataSourceSelectionWidget,
    NamedDataSource,
    NamedDataSourceImportError,
    get_structured_items,
)
from wcs.data_sources_agendas import RefreshAgendas, has_chrono
from wcs.formdef_base import get_formdefs_of_all_kinds
from wcs.qommon import _, errors, misc, pgettext, template
from wcs.qommon.errors import AccessForbiddenError
from wcs.qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    DurationWidget,
    FileWidget,
    Form,
    HtmlWidget,
    SingleSelectWidget,
    SlugWidget,
    StringWidget,
    WidgetDict,
    WidgetList,
    get_response,
    get_session,
)
from wcs.roles import get_user_roles


class NamedDataSourceUI:
    def __init__(self, datasource):
        self.datasource = datasource
        if self.datasource is None:
            self.datasource = NamedDataSource()

    def get_form(self):
        form = Form(enctype='multipart/form-data', use_tabs=True)
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=30, value=self.datasource.name)
        category_options = get_categories(DataSourceCategory)
        if category_options and (not self.datasource or self.datasource.type != 'wcs:users'):
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
                value=self.datasource.category_id,
            )
        if not self.datasource or (
            self.datasource.type != 'wcs:users' and self.datasource.external != 'agenda_manual'
        ):
            form.add(
                DataSourceSelectionWidget,
                'data_source',
                value=self.datasource.data_source,
                title=_('Data Source'),
                allowed_source_types={'json', 'jsonp', 'geojson', 'jsonvalue'},
                required=True,
            )
            form.add(
                DurationWidget,
                'cache_duration',
                value=self.datasource.cache_duration,
                title=_('Cache Duration'),
                hint=_(
                    'Caching data will improve performances but will keep changes '
                    'from being visible immediately.  You should keep this duration '
                    'reasonably short.'
                ),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value-in': 'json|geojson',
                },
            )
            form.add(
                StringWidget,
                'query_parameter',
                value=self.datasource.query_parameter,
                title=_('Query Parameter'),
                hint=_('Name of the parameter to use for querying source (typically, q)'),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'json',
                },
            )
            form.add(
                StringWidget,
                'id_parameter',
                value=self.datasource.id_parameter,
                title=_('Id Parameter'),
                hint=_('Name of the parameter to use to get a given entry from data source (typically, id)'),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'json',
                },
            )
            form.add(
                StringWidget,
                'id_property',
                value=self.datasource.id_property,
                title=_('Id Property'),
                hint=_('Name of the property to use to get a given entry from data source (default: id)'),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'geojson',
                },
            )
            form.add(
                StringWidget,
                'label_template_property',
                value=self.datasource.label_template_property,
                title=_('Label template'),
                hint=_('Django expression to build label of each value (default: {{ text }})'),
                required=False,
                advanced=True,
                size=80,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'geojson',
                },
            )
        if self.datasource and self.datasource.type == 'wcs:users':
            options = [(None, '---', None)]
            options += get_user_roles()
            form.add(
                WidgetList,
                'users_included_roles',
                element_type=SingleSelectWidget,
                value=self.datasource.users_included_roles,
                title=_('Users with roles'),
                add_element_label=_('Add Role'),
                element_kwargs={'render_br': False, 'options': options},
            )
            form.add(
                WidgetList,
                'users_excluded_roles',
                element_type=SingleSelectWidget,
                value=self.datasource.users_excluded_roles,
                title=_('Users without roles'),
                add_element_label=_('Add Role'),
                element_kwargs={'render_br': False, 'options': options},
            )
            form.add(
                CheckboxWidget,
                'include_disabled_users',
                title=_('Include disabled users'),
                value=self.datasource.include_disabled_users,
            )
        if self.datasource.slug and not self.datasource.is_used():
            form.add(
                SlugWidget,
                'slug',
                value=self.datasource.slug,
                advanced=True,
            )
        if not self.datasource or self.datasource.type != 'wcs:users':
            form.add(
                StringWidget,
                'data_attribute',
                value=self.datasource.data_attribute,
                title=_('Data Attribute'),
                hint=_(
                    'Name of the attribute containing the list of results (default: data). '
                    'Possibility to chain attributes with a dot separator (example: data.results)'
                ),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'json',
                },
            )
            form.add(
                StringWidget,
                'id_attribute',
                value=self.datasource.id_attribute,
                title=_('Id Attribute'),
                hint=_('Name of the attribute containing the identifier of an entry (default: id)'),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'json',
                },
            )
            form.add(
                StringWidget,
                'text_attribute',
                value=self.datasource.text_attribute,
                title=_('Text Attribute'),
                hint=_('Name of the attribute containing the label of an entry (default: text)'),
                required=False,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'data_source$type',
                    'data-dynamic-display-value': 'json',
                },
            )
        if not self.datasource or self.datasource.type != 'wcs:users':
            form.add(
                CheckboxWidget,
                'notify_on_errors',
                title=_('Notify on errors'),
                value=self.datasource.notify_on_errors,
            )
            form.add(
                CheckboxWidget,
                'record_on_errors',
                title=_('Record on errors'),
                value=self.datasource.record_on_errors,
            )
        if self.datasource.external == 'agenda_manual':
            form.add(
                WidgetDict,
                'qs_data',
                title=_('Query string data'),
                value=self.datasource.qs_data or {},
                element_value_type=ComputedExpressionWidget,
                allow_empty_values=True,
                value_for_empty_value='',
            )

        if not self.datasource.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        name = form.get_widget('name').parse()
        slug_widget = form.get_widget('slug')
        if slug_widget:
            slug = form.get_widget('slug').parse()

        for nds in NamedDataSource.select():
            if nds.id == self.datasource.id:
                continue
            if name == nds.name:
                form.get_widget('name').set_error(_('This name is already used.'))
            if slug_widget and slug == nds.slug:
                slug_widget.set_error(_('This value is already used.'))
        if form.has_errors():
            raise ValueError()

        self.datasource.name = name
        if slug_widget:
            self.datasource.slug = slug

        for widget in form.widgets:
            if widget.name in ('name', 'slug'):
                continue
            setattr(self.datasource, widget.name, widget.parse())

        self.datasource.store()


class NamedDataSourcePage(Directory, DocumentableMixin):
    _q_exports = [
        '',
        'edit',
        'delete',
        'export',
        'duplicate',
        ('invalidate-cache', 'invalidate_cache'),
        ('history', 'snapshots_dir'),
        ('preview-block', 'preview_block'),
        ('update-documentation', 'update_documentation'),
    ]
    do_not_call_in_templates = True

    def __init__(self, component=None, instance=None):
        try:
            self.datasource = instance or NamedDataSource.get(component)
        except KeyError:
            raise errors.TraversalError()

        if not self.datasource.category and not DataSourceCategory.has_global_access():
            raise errors.AccessForbiddenError()
        if self.datasource.category and not self.datasource.category.is_managed_by_user():
            raise errors.AccessForbiddenError()

        if self.datasource.external == 'agenda':
            self.datasource.readonly = True
        self.datasource_ui = NamedDataSourceUI(self.datasource)
        get_response().breadcrumb.append((component + '/', self.datasource.name))
        self.snapshots_dir = SnapshotsDirectory(self.datasource)
        self.documented_object = self.datasource
        self.documented_element = self.datasource

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(self.datasource.name)
        url = None
        if self.datasource.data_source and self.datasource.data_source.get('type') in (
            'json',
            'jsonp',
            'geojson',
        ):
            try:
                url = self.datasource.get_variadic_url()
            except Exception as exc:
                url = '#%s' % exc
        if not self.datasource.is_readonly():
            Application.load_for_object(self.datasource)
        get_response().add_javascript(['popup.js'])
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/data-source.html'],
            context={
                'view': self,
                'datasource': self.datasource,
                'roles': get_user_roles(),
                'url': url,
                'has_sidebar': True,
            },
            is_django_native=True,
        )

    def snapshot_info_block(self):
        if hasattr(self.datasource, 'snapshot_object'):
            return utils.snapshot_info_block(snapshot=self.datasource.snapshot_object)
        return ''

    def usage_in_formdefs(self):
        fields = []
        for formdef in get_formdefs_of_all_kinds():
            fields.extend(list(self.datasource.usage_in_formdef(formdef)))
        fields.sort(key=lambda x: x._formdef.name.lower())
        return fields

    def has_preview_block(self):
        return bool(self.datasource.type in ('json', 'geojson', 'jsonvalue', 'wcs:users'))

    def preview_block(self):
        get_request().disable_error_notifications = True
        get_request().ignore_session = True
        get_response().raw = True
        data_source = self.datasource.extended_data_source
        try:
            items = get_structured_items({'type': self.datasource.slug})
        except Exception as exc:
            return htmltext('<div class="warningnotice">%s (%r)</div>') % (
                _('Unexpected fatal error getting items for preview.'),
                exc,
            )
        if not items:
            return ''
        r = TemplateIO(html=True)
        r += htmltext('<ul>')
        additional_keys = set()
        for item in items[:10]:
            if not isinstance(item.get('text'), str):
                r += htmltext('<li><tt>%s</tt>: <i>%s (%r)</i></li>') % (
                    item.get('id'),
                    _('error: not a string'),
                    item.get('text'),
                )
            else:
                r += htmltext('<li><tt>%s</tt>: %s</li>') % (item.get('id'), item.get('text'))
                if data_source.get('type') == 'geojson':
                    additional_keys.add('geometry_coordinates')
                    additional_keys.add('geometry_type')
                    additional_keys |= {'properties_%s' % k for k in item.get('properties', {}).keys()}
                else:
                    additional_keys |= set(item.keys())
        if len(items) > 10:
            r += htmltext('<li>...</li>')
        r += htmltext('</ul>')
        additional_keys -= {'id', 'text', 'properties_id', 'properties_text'}
        if additional_keys:
            r += htmltext('<p>%s %s</p>') % (
                _('Additional keys are available:'),
                ', '.join(sorted(additional_keys)),
            )
        return r.getvalue()

    def edit(self):
        form = self.datasource_ui.get_form()
        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                self.datasource_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().breadcrumb.append(('edit', _('Edit')))
        get_response().set_title(_('Edit Data Source'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Data Source')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        if not self.datasource.is_used():
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this data source.'))
            )
            form.add_submit('delete', _('Delete'))
        else:
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('This datasource is still used, it cannot be deleted.'))
            )
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Data Source'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Data Source:'), self.datasource.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.datasource)
        self.datasource.remove_self()
        return redirect('..')

    def export(self):
        return misc.xml_response(
            self.datasource,
            filename='datasource-%s.wcs' % self.datasource.slug,
            content_type='application/x-wcs-datasource',
        )

    def duplicate(self):
        if hasattr(self.datasource, 'snapshot_object'):
            return redirect('.')

        form = Form(enctype='multipart/form-data')
        form.add_submit('duplicate', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            get_response().set_title(_('Duplicate Data Source'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Duplicating Data Source:'), self.datasource.name)
            r += form.render()
            return r.getvalue()

        tree = self.datasource.export_to_xml(include_id=True)
        new_datasource = NamedDataSource.import_from_xml_tree(tree)
        new_datasource.name = _('Copy of %s') % new_datasource.name
        new_datasource.slug = new_datasource.get_new_slug(new_datasource.slug)
        if self.datasource.agenda_ds:
            new_datasource.external = 'agenda_manual'
        new_datasource.store()
        return redirect('../%s' % new_datasource.id)

    def invalidate_cache(self):
        self.datasource.store()  # no change -> no snapshot but the stored file will have a new timestamp
        get_session().add_message(_('This datasource cache has been invalidated.'), level='info')
        return redirect('.')


class NamedDataSourcesDirectory(Directory):
    _q_exports = [
        '',
        'new',
        ('new-users', 'new_users'),
        'categories',
        ('import', 'p_import'),
        ('sync-agendas', 'sync_agendas'),
        ('application', 'applications_dir'),
        ('by-slug', 'by_slug'),
    ]
    do_not_call_in_templates = True
    by_slug = utils.BySlugDirectory(klass=NamedDataSource)
    categories = DataSourceCategoriesDirectory()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(NamedDataSource)

    def is_accessible(self, user=None):
        return DataSourceCategory.has_global_access() or any(DataSourceCategory.select_for_user())

    def _q_traverse(self, path):
        if not self.is_accessible():
            raise AccessForbiddenError()
        get_response().breadcrumb.append(('data-sources/', _('Data Sources')))
        return super()._q_traverse(path)

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Data Sources'))
        get_response().add_javascript(['popup.js'])
        context = {
            'view': self,
            'has_chrono': has_chrono(get_publisher()),
            'has_users': True,
            'applications': Application.select_for_object_type(NamedDataSource.xml_root_node),
            'elements_label': NamedDataSource.verbose_name_plural,
            'has_sidebar': True,
            'is_global_accessible_categories': get_publisher()
            .get_backoffice_root()
            .is_accessible('categories'),
        }
        data_sources = NamedDataSource.select(order_by='name')
        Application.populate_objects(data_sources)
        context.update(self.get_list_context(data_sources))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/data-sources.html'],
            context=context,
            is_django_native=True,
        )

    def get_list_context(self, objects, application=Ellipsis):
        from wcs.applications import Application

        data_sources = []
        user_data_sources = []
        agenda_data_sources = []
        for ds in objects:
            if ds.external == 'agenda':
                agenda_data_sources.append(ds)
            elif ds.type == 'wcs:users':
                user_data_sources.append(ds)
            else:
                data_sources.append(ds)
        categories = DataSourceCategory.select_for_user()
        DataSourceCategory.sort_by_position(categories)
        if categories:
            if DataSourceCategory.has_global_access():
                categories.append(DataSourceCategory(pgettext('categories', 'Uncategorised')))
            for category in categories:
                category.data_sources = [x for x in data_sources if x.category_id == category.id]
        generated_data_sources = list(CardDef.get_carddefs_as_data_source())
        generated_data_sources.sort(key=lambda x: misc.simplify(x[1]))
        if application is None:
            carddefs = Application.get_orphan_objects_for_object_type(CardDef.xml_root_node)
            generated_data_sources = [g for g in generated_data_sources if g[0] in carddefs]
        elif application is not Ellipsis:
            carddefs = application.get_objects_for_object_type(CardDef.xml_root_node)
            generated_data_sources = [g for g in generated_data_sources if g[0] in carddefs]
        else:
            Application.populate_objects([g[0] for g in generated_data_sources])
        carddef_categories = CardDefCategory.select_for_user()
        CardDefCategory.sort_by_position(carddef_categories)
        if carddef_categories:
            carddef_categories.append(CardDefCategory(pgettext('categories', 'Uncategorised')))
            for carddef_category in carddef_categories:
                carddef_category.generated_data_sources = [
                    x for x in generated_data_sources if x[0].category_id == carddef_category.id
                ]
        return {
            'data_sources': data_sources,
            'categories': categories,
            'user_data_sources': user_data_sources,
            'agenda_data_sources': agenda_data_sources,
            'generated_data_sources': generated_data_sources,
            'carddef_categories': carddef_categories,
        }

    def _new(self, url, breadcrumb, title, ds_type=None):
        get_response().breadcrumb.append((url, breadcrumb))
        datasource = NamedDataSource()
        if ds_type is not None:
            datasource.data_source = {'type': ds_type}
        datasource_ui = NamedDataSourceUI(datasource)
        form = datasource_ui.get_form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                datasource_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().set_title(title)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % title
        r += form.render()
        return r.getvalue()

    def new(self):
        return self._new(url='new', breadcrumb=_('New'), title=_('New Data Source'))

    def new_users(self):
        return self._new(
            url='new-users',
            breadcrumb=_('New Users Data Source'),
            title=_('New Users Data Source'),
            ds_type='wcs:users',
        )

    def _q_lookup(self, component):
        return NamedDataSourcePage(component)

    def p_import(self):
        form = Form(enctype='multipart/form-data')
        import_title = _('Import Data Source')

        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', import_title)
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(import_title)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % import_title
        r += htmltext('<p>%s</p>') % _('You can install a new data source by uploading a file.')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp

        error, reason = False, None
        try:
            datasource = NamedDataSource.import_from_xml(fp, check_deprecated=True)
            get_session().add_message(_('This datasource has been successfully imported.'), level='info')
        except NamedDataSourceImportError as e:
            error = True
            reason = str(e)
        except ValueError:
            error = True

        if not error and not DataSourceCategory.has_global_access():
            management_roles = {x.id for x in getattr(datasource.category, 'management_roles', None) or []}
            user_roles = set(get_request().user.get_roles())
            if not user_roles.intersection(management_roles):
                error = True
                reason = _('unauthorized category')

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            form.set_error('file', msg)
            raise ValueError()

        try:
            # check slug unicity
            NamedDataSource.get_on_index(datasource.slug, 'slug', ignore_migration=True)
        except KeyError:
            pass
        else:
            datasource.slug = None  # a new one will be set in .store()
        datasource.store()
        return redirect('%s/' % datasource.id)

    def sync_agendas(self):
        job = get_publisher().add_after_job(RefreshAgendas())
        get_session().add_message(
            _('Agendas will be updated in the background.'), level='info', job_id=job.id
        )
        return redirect('.')
