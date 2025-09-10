# w.c.s. - web application for online forms
# Copyright (C) 2005-2016  Entr'ouvert
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

import re

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.documentable import DocumentableMixin
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.qommon import _, errors, misc, template
from wcs.qommon.form import CheckboxWidget, FileWidget, Form, HtmlWidget, SlugWidget, StringWidget
from wcs.utils import grep_strings
from wcs.wscalls import NamedWsCall, NamedWsCallImportError, WsCallRequestWidget


class NamedWsCallUI:
    def __init__(self, wscall):
        self.wscall = wscall
        if self.wscall is None:
            self.wscall = NamedWsCall()

    def get_form(self):
        form = Form(enctype='multipart/form-data', use_tabs=True)
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=30, value=self.wscall.name)
        if self.wscall.slug:
            form.add(
                SlugWidget,
                'slug',
                value=self.wscall.slug,
                hint=_('Beware it is risky to change it'),
                advanced=True,
            )
        form.add(WsCallRequestWidget, 'request', value=self.wscall.request, title=_('Request'), required=True)

        form.widgets.append(
            HtmlWidget(
                '<div class="infonotice"><p>%s</p></div>'
                % _(
                    'This tab is about connection, payload, and HTTP errors. '
                    'Application errors ("err" property in response different than zero) '
                    'are always silent.'
                ),
                tab=('error', _('Error Handling')),
            )
        )
        form.add(
            CheckboxWidget,
            'notify_on_errors',
            title=_('Notify on errors'),
            value=self.wscall.notify_on_errors,
            tab=('error', _('Error Handling')),
        )
        form.add(
            CheckboxWidget,
            'record_on_errors',
            title=_('Record on errors'),
            value=self.wscall.record_on_errors if self.wscall.slug else True,
            default_value=True,
            tab=('error', _('Error Handling')),
        )
        if not self.wscall.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        name = form.get_widget('name').parse()
        if self.wscall.slug:
            slug = form.get_widget('slug').parse()
        else:
            slug = None

        for wscall in NamedWsCall.select():
            if wscall.id == self.wscall.id:
                continue
            if name == wscall.name:
                form.get_widget('name').set_error(_('This name is already used.'))
            if slug == wscall.slug:
                form.get_widget('slug').set_error(_('This value is already used.'))
        if form.has_errors():
            raise ValueError()

        self.wscall.name = name
        self.wscall.notify_on_errors = form.get_widget('notify_on_errors').parse()
        self.wscall.record_on_errors = form.get_widget('record_on_errors').parse()
        self.wscall.request = form.get_widget('request').parse()
        if self.wscall.slug:
            self.wscall.slug = slug
        self.wscall.store()


class NamedWsCallPage(Directory, DocumentableMixin):
    do_not_call_in_templates = True
    _q_exports = [
        '',
        'edit',
        'delete',
        'duplicate',
        'export',
        ('history', 'snapshots_dir'),
        'usage',
        ('update-documentation', 'update_documentation'),
    ]

    def __init__(self, component, instance=None):
        try:
            if instance:
                self.wscall = instance
            elif misc.is_ascii_digit(component):
                self.wscall = NamedWsCall.get(component)
            else:
                self.wscall = NamedWsCall.get_by_slug(component)
        except KeyError:
            raise errors.TraversalError()
        self.wscall_ui = NamedWsCallUI(self.wscall)
        get_response().breadcrumb.append((component + '/', self.wscall.name))
        self.snapshots_dir = SnapshotsDirectory(self.wscall)
        self.documented_object = self.wscall
        self.documented_element = self.wscall

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(self.wscall.name)
        if not self.wscall.is_readonly():
            Application.load_for_object(self.wscall)
        get_response().add_javascript(['popup.js'])
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/wscall.html'],
            context={'view': self, 'wscall': self.wscall, 'has_sidebar': True},
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(snapshot=self.wscall.snapshot_object)

    def usage(self):
        get_request().disable_error_notifications = True
        get_request().ignore_session = True
        get_response().raw = True

        usage = {}

        def accumulate(source_url, value, source_name):
            usage[source_url] = source_name

        grep_strings(re.compile(r'\bwebservice\.%s\b' % self.wscall.slug), hit_function=accumulate)
        grep_strings(re.compile(r'{%% +webservice +[\'"]%s[\'"]' % self.wscall.slug), hit_function=accumulate)
        r = TemplateIO(html=True)
        if usage:
            for source_url, source_name in usage.items():
                r += htmltext(f'<li><a href="{source_url}">%s</a></li>\n') % source_name
        else:
            r += htmltext('<li class="list-item-no-usage"><p>%s</p></li>') % _('No usage detected.')
        return r.getvalue()

    def edit(self):
        form = self.wscall_ui.get_form()
        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                self.wscall_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('../%s/' % self.wscall.id)

        get_response().breadcrumb.append(('edit', _('Edit')))
        get_response().add_javascript(['jquery-ui.js'])
        get_response().set_title(_('Edit webservice call'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit webservice call')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this webservice call.'))
        )
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete webservice call'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting webservice call:'), self.wscall.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.wscall)
        self.wscall.remove_self()
        return redirect('..')

    def export(self):
        return misc.xml_response(
            self.wscall, filename='wscall-%s.wcs' % self.wscall.slug, content_type='application/x-wcs-wscall'
        )

    def duplicate(self):
        if hasattr(self.wscall, 'snapshot_object'):
            return redirect('.')

        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        known_names = {x.name for x in NamedWsCall.select()}

        if form.is_submitted():
            if name_widget.parse() in known_names:
                name_widget.set_error(_('This name is already used.'))
        else:
            original_name = self.wscall.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            no = 2
            while new_name in known_names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            get_response().set_title(_('Duplicate webservice call'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Duplicate webservice call')
            r += form.render()
            return r.getvalue()

        tree = self.wscall.export_to_xml(include_id=True)
        new_wscall = NamedWsCall.import_from_xml_tree(tree)
        new_wscall.name = name_widget.parse()
        new_wscall.slug = new_wscall.get_new_slug(new_wscall.name)
        new_wscall.store()
        return redirect('../%s/' % new_wscall.id)


class NamedWsCallsDirectory(Directory):
    _q_exports = ['', 'new', ('import', 'p_import'), ('application', 'applications_dir')]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(NamedWsCall)

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('wscalls/', _('Webservice Calls')))
        return super()._q_traverse(path)

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Webservice Calls'))
        get_response().add_javascript(['popup.js'])
        wscalls = NamedWsCall.select(order_by='name')
        Application.populate_objects(wscalls)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/wscalls.html'],
            context={
                'view': self,
                'wscalls': wscalls,
                'applications': Application.select_for_object_type(NamedWsCall.xml_root_node),
                'elements_label': NamedWsCall.verbose_name_plural,
                'has_sidebar': True,
            },
            is_django_native=True,
        )

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        wscall_ui = NamedWsCallUI(None)
        form = wscall_ui.get_form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                wscall_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().set_title(_('New webservice call'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New webservice call')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        return NamedWsCallPage(component)

    def p_import(self):
        form = Form(enctype='multipart/form-data')
        import_title = _('Import webservice call')

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
        r += htmltext('<p>%s</p>') % _('You can install a new webservice call by uploading a file.')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp

        error, reason = False, None
        try:
            wscall = NamedWsCall.import_from_xml(fp, check_deprecated=True)
            get_session().add_message(_('This webservice call has been successfully imported.'), level='info')
        except NamedWsCallImportError as e:
            error = True
            reason = str(e)
        except ValueError:
            error = True

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            form.set_error('file', msg)
            raise ValueError()

        # check slug unicity
        if NamedWsCall.get_by_slug(wscall.slug):
            wscall.slug = None  # a new one will be set in .store()
        wscall.store()
        return redirect('%s/' % wscall.id)
