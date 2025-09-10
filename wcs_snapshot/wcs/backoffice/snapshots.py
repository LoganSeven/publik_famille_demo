# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

import difflib
import re

from django.utils.module_loading import import_string
from lxml.html.diff import htmldiff  # pylint: disable=no-name-in-module
from pyquery import PyQuery as pq
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.formdef_base import FormdefImportError
from wcs.qommon import _, errors, misc, template
from wcs.qommon.form import Form, RadiobuttonsWidget, StringWidget
from wcs.sql_criterias import Equal
from wcs.workflows import WorkflowImportError


class SnapshotsDirectory(Directory):
    _q_exports = ['', 'save', 'compare']
    do_not_call_in_templates = True

    def __init__(self, instance):
        self.obj = instance
        self.object_type = instance.xml_root_node
        self.object_id = instance.id

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('history/', _('History')))
        return super()._q_traverse(path)

    def _q_index(self):
        from wcs.testdef import TestDef

        get_response().set_title(_('History'))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/snapshots.html'],
            context={
                'view': self,
                'form_has_tests': self.object_type in ('formdef', 'carddef')
                and bool(TestDef.select_for_objectdef(self.obj)),
            },
        )

    def save(self):
        form = Form(enctype='multipart/form-data')
        label = form.add(StringWidget, 'label', title=_('Label'), required=True)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('../')

        if form.is_submitted() and not form.has_errors():
            get_publisher().snapshot_class.snap(instance=self.obj, label=label.parse())
            return redirect('../')

        get_response().set_title(_('History'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Save snapshot')
        r += form.render()
        return r.getvalue()

    def get_snapshots_from_application(self):
        version1 = get_request().form.get('version1')
        version2 = get_request().form.get('version2')
        if not version1 or not version2:
            raise errors.TraversalError()

        def get_snapshots(version):
            return get_publisher().snapshot_class.select(
                [
                    Equal('object_type', self.object_type),
                    Equal('object_id', str(self.object_id)),
                    Equal('application_slug', get_request().form['application']),
                    Equal('application_version', version),
                ],
                order_by='-timestamp',
            )

        snapshots_for_app1 = get_snapshots(version1)
        snapshots_for_app2 = get_snapshots(version2)
        if not snapshots_for_app1 or not snapshots_for_app2:
            return None, None

        return snapshots_for_app1[0], snapshots_for_app2[0]

    def get_snapshots(self):
        if 'application' in get_request().form:
            return self.get_snapshots_from_application()

        id1 = get_request().form.get('version1')
        id2 = get_request().form.get('version2')
        if not id1 or not id2:
            raise errors.TraversalError()

        snapshot1 = get_publisher().snapshot_class.get(id1, ignore_errors=True)
        snapshot2 = get_publisher().snapshot_class.get(id2, ignore_errors=True)
        return snapshot1, snapshot2

    def compare(self):
        get_response().breadcrumb.append(('compare/', _('Compare')))
        get_response().set_title(_('Compare'))

        mode = get_request().form.get('mode') or 'xml'
        if mode not in ['xml', 'inspect']:
            raise errors.TraversalError()

        snapshot1, snapshot2 = self.get_snapshots()
        if not snapshot1 or not snapshot2:
            if 'application' in get_request().form:
                return redirect('.')
            raise errors.TraversalError()
        if snapshot1.timestamp > snapshot2.timestamp:
            snapshot1, snapshot2 = snapshot2, snapshot1

        klass = snapshot1.get_object_class()
        backoffice_class = import_string(klass.backoffice_class)
        has_inspect = hasattr(backoffice_class, 'render_inspect')

        if mode == 'inspect' and not has_inspect:
            raise errors.TraversalError()

        from wcs.blocks import BlockdefImportError

        try:
            context = getattr(self, 'get_compare_%s_context' % mode)(snapshot1, snapshot2)
        except (BlockdefImportError, FormdefImportError, WorkflowImportError, RecursionError) as e:
            return template.error_page(_('Can not display snapshot (%s)') % e)

        context.update(
            {
                'mode': mode,
                'has_inspect': has_inspect,
                'snapshot1': snapshot1,
                'snapshot2': snapshot2,
            }
        )
        get_response().add_javascript(['gadjo.snapshotdiff.js'])
        get_response().add_css_include('gadjo.snapshotdiff.css')
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/snapshots_compare.html'],
            context=context,
        )

    def snapshot_desc(self, snapshot):
        label_or_comment = ''
        if snapshot.label:
            label_or_comment = snapshot.label
        elif snapshot.comment:
            label_or_comment = snapshot.comment
        if snapshot.application_version:
            label_or_comment += ' (%s)' % _('Version %s') % snapshot.application_version
        return '{name} <a href="{pk}/view/">{pk}</a> - {label_or_comment} ({user}{timestamp})'.format(
            name=_('Snapshot'),
            pk=snapshot.id,
            label_or_comment=label_or_comment,
            user='%s ' % snapshot.user if snapshot.user_id else '',
            timestamp=misc.strftime(misc.datetime_format(), snapshot.timestamp),
        )

    def get_compare_xml_context(self, snapshot1, snapshot2):
        serialization1 = snapshot1.get_serialization(indented=True)
        serialization2 = snapshot2.get_serialization(indented=True)
        diff_serialization = difflib.HtmlDiff(wrapcolumn=160).make_table(
            fromlines=serialization1.splitlines(True),
            tolines=serialization2.splitlines(True),
        )

        return {
            'fromdesc': self.snapshot_desc(snapshot1),
            'todesc': self.snapshot_desc(snapshot2),
            'diff_serialization': diff_serialization,
        }

    def get_compare_inspect_context(self, snapshot1, snapshot2):
        klass = snapshot1.get_object_class()
        backoffice_class = import_string(klass.backoffice_class)

        def clean_panel(tab):
            panel = pq(tab)
            # remove quicknavs
            panel.find('.inspect--quicknav').remove()
            # remove page & field counters, for formdef
            panel.find('.page-field-counters').remove()
            # remove status colors
            panel.find('.inspect-status--colour').remove()
            return panel.html().strip('\n')

        def fix_result(panel_diff):
            if not panel_diff:
                return panel_diff
            panel = pq(panel_diff)
            # remove "Link" added by htmldiff
            for link in panel.find('a'):
                d = pq(link)
                text = d.html()
                new_text = re.sub(r' Link: .*$', '', text)
                d.html(new_text)
            # remove empty ins and del tags
            for elem in panel.find('ins, del'):
                d = pq(elem)
                if not (d.html() or '').strip():
                    d.remove()
            # prevent auto-closing behaviour of pyquery .html() method
            for elem in panel.find('span, ul, div'):
                d = pq(elem)
                if not d.html():
                    d.html(' ')
            # sometimes status section are misplaced by htmldiff, fix it
            for elem in panel.find('div.section.status'):
                d = pq(elem)
                parents = d.parents('div.section.status')
                if parents:
                    pq(parents[0]).after(d.remove())
            return panel.html()

        inspect1 = backoffice_class(component=None, instance=snapshot1.instance).render_inspect()
        inspect1.context['snapshots_diff'] = True
        inspect1 = template.render(inspect1.templates, inspect1.context)
        d1 = pq(str(inspect1))
        inspect2 = backoffice_class(component=None, instance=snapshot2.instance).render_inspect()
        inspect2.context['snapshots_diff'] = True
        inspect2 = template.render(inspect2.templates, inspect2.context)
        d2 = pq(str(inspect2))
        panels_attrs = [tab.attrib for tab in d1('[role="tabpanel"]')]
        panels1 = [clean_panel(tab) for tab in d1('[role="tabpanel"]')]
        panels2 = [clean_panel(tab) for tab in d2('[role="tabpanel"]')]

        # build tab list (merge version 1 and version2)
        tabs1 = d1.find('[role="tab"]')
        tabs2 = d2.find('[role="tab"]')
        tabs_order = [t.get('id') for t in panels_attrs]
        tabs = {}
        for tab in tabs1 + tabs2:
            tab_id = pq(tab).attr('aria-controls')
            tabs[tab_id] = pq(tab).outer_html()
        tabs = [tabs[k] for k in tabs_order if k in tabs]

        # build diff of each panel
        panels_diff = list(map(htmldiff, panels1, panels2))
        panels_diff = [fix_result(t) for t in panels_diff]

        return {
            'fromdesc': self.snapshot_desc(snapshot1),
            'todesc': self.snapshot_desc(snapshot2),
            'tabs': tabs,
            'panels': zip(panels_attrs, panels_diff),
            'tab_class_names': d1('.pk-tabs').attr('class'),
        }

    def snapshots(self):
        from wcs.testdef import TestResults

        current_date = None
        snapshots = get_publisher().snapshot_class.select_object_history(self.obj)
        test_results = TestResults.select(
            [Equal('object_type', self.obj.get_table_name()), Equal('object_id', str(self.obj.id))]
        )
        test_results_by_id = {x.id: x for x in test_results}
        day_snapshot = None
        for snapshot in snapshots:
            if snapshot.timestamp.date() != current_date:
                current_date = snapshot.timestamp.date()
                snapshot.new_day = True
                snapshot.day_other_count = 0
                day_snapshot = snapshot
            else:
                day_snapshot.day_other_count += 1
            snapshot.test_results = test_results_by_id.get(snapshot.test_results_id)
        return snapshots

    def _q_lookup(self, component):
        snapshot = get_publisher().snapshot_class.get(component, ignore_errors=True)
        if not snapshot or not snapshot.is_from_object(self.obj):
            raise errors.TraversalError()
        snapshot_directory_class = getattr(self, 'snapshot_directory_class', SnapshotDirectory)
        return snapshot_directory_class(self.obj, snapshot)


class SnapshotDirectory(Directory):
    _q_exports = ['', 'export', 'restore', 'view', 'inspect']

    allow_restore_as_new = True

    def __init__(self, instance, snapshot):
        self.obj = instance
        self.snapshot = snapshot

    def _q_traverse(self, path):
        get_response().breadcrumb.append(
            ('%s/' % self.snapshot.id, misc.localstrftime(self.snapshot.timestamp))
        )
        return super()._q_traverse(path)

    def _q_index(self):
        return redirect('view/')

    def export(self):
        response = get_response()
        response.set_content_type('application/x-wcs-snapshot')
        response.set_header(
            'content-disposition',
            'attachment; filename=snapshot-%s-%s-%s.wcs'
            % (
                self.snapshot.object_type,
                self.snapshot.id,
                self.snapshot.timestamp.strftime('%Y%m%d-%H%M'),
            ),
        )
        return '<?xml version="1.0"?>\n' + self.snapshot.get_serialization()

    def restore(self):
        from wcs.blocks import BlockdefImportError

        form = Form(enctype='multipart/form-data')

        action_options = [
            ('overwrite', _('Overwrite current content'), 'overwrite'),
        ]
        if self.allow_restore_as_new:
            action_options.insert(0, ('as-new', _('Restore as a new item'), 'as-new'))

        action = form.add(
            RadiobuttonsWidget,
            'action',
            options=action_options,
            value=action_options[0][0],
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_submit() == 'cancel':
            return redirect('..')

        if form.get_submit() == 'submit':
            try:
                self.snapshot.restore(as_new=bool(action.parse() == 'as-new'))
            except (BlockdefImportError, FormdefImportError, WorkflowImportError) as e:
                reason = _(e.msg) % e.msg_args
                if e.details:
                    reason += ' [%s]' % e.details
                error_msg = _('Can not restore snapshot (%s)') % reason
                form.set_error('action', error_msg)
            else:
                return redirect(self.snapshot.instance.get_admin_url())

        get_response().breadcrumb.append(('restore', _('Restore')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Restore snapshot')
        r += form.render()
        return r.getvalue()

    @property
    def view(self):
        from wcs.blocks import BlockdefImportError

        klass = self.snapshot.get_object_class()
        self.snapshot._check_datasources = False
        try:
            instance = self.snapshot.instance
        except (BlockdefImportError, FormdefImportError, WorkflowImportError) as e:
            reason = _(e.msg) % e.msg_args
            if e.details:
                reason += ' [%s]' % e.details
            error_msg = _('Can not display snapshot (%s)') % reason
            get_session().add_message(error_msg)

            class RedirectDirectory(Directory):
                def _q_lookup(self, component):
                    return redirect('../../')

            return RedirectDirectory()

        backoffice_class = import_string(klass.backoffice_class)
        return backoffice_class(component='view', instance=instance)

    def inspect(self):
        from wcs.blocks import BlockdefImportError

        klass = self.snapshot.get_object_class()
        self.snapshot._check_datasources = False
        try:
            instance = self.snapshot.instance
        except (BlockdefImportError, FormdefImportError, WorkflowImportError) as e:
            reason = _(e.msg) % e.msg_args
            if e.details:
                reason += ' [%s]' % e.details
            error_msg = _('Can not inspect snapshot (%s)') % reason
            get_session().add_message(error_msg)

            return redirect('../')

        backoffice_class = import_string(klass.backoffice_class)
        has_inspect = hasattr(backoffice_class, 'render_inspect')
        if not has_inspect:
            raise errors.TraversalError()
        return backoffice_class(component=None, instance=instance).inspect()
