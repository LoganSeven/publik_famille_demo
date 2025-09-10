# lingo - payment and billing system
# Copyright (C) 2022-2024  Entr'ouvert
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


import difflib
import json
import re

from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template import loader
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView
from lxml.html.diff import htmldiff
from pyquery import PyQuery as pq


class InstanceWithSnapshotHistoryView(ListView):
    def get_queryset(self):
        self.instance = get_object_or_404(self.model.get_instance_model(), pk=self.kwargs['pk'])
        return self.instance.instance_snapshots.all().defer('serialization').select_related('user')

    def get_context_data(self, **kwargs):
        kwargs[self.instance_context_key] = self.instance
        kwargs['object'] = self.instance
        current_date = None
        context = super().get_context_data(**kwargs)
        day_snapshot = None
        for snapshot in context['object_list']:
            if snapshot.timestamp.date() != current_date:
                current_date = snapshot.timestamp.date()
                snapshot.new_day = True
                snapshot.day_other_count = 0
                day_snapshot = snapshot
            else:
                day_snapshot.day_other_count += 1
        return context


class InstanceWithSnapshotHistoryCompareView(DetailView):
    def get_snapshots_from_application(self):
        version1 = self.request.GET.get('version1')
        version2 = self.request.GET.get('version2')
        if not version1 or not version2:
            raise Http404

        snapshot_for_app1 = (
            self.model.get_snapshot_model()
            .objects.filter(
                instance=self.object,
                application_slug=self.request.GET['application'],
                application_version=self.request.GET['version1'],
            )
            .order_by('timestamp')
            .last()
        )
        snapshot_for_app2 = (
            self.model.get_snapshot_model()
            .objects.filter(
                instance=self.object,
                application_slug=self.request.GET['application'],
                application_version=self.request.GET['version2'],
            )
            .order_by('timestamp')
            .last()
        )
        return snapshot_for_app1, snapshot_for_app2

    def get_snapshots(self):
        if 'application' in self.request.GET:
            return self.get_snapshots_from_application()

        id1 = self.request.GET.get('version1')
        id2 = self.request.GET.get('version2')
        if not id1 or not id2:
            raise Http404

        snapshot1 = get_object_or_404(self.model.get_snapshot_model(), pk=id1, instance=self.object)
        snapshot2 = get_object_or_404(self.model.get_snapshot_model(), pk=id2, instance=self.object)

        return snapshot1, snapshot2

    def get_context_data(self, **kwargs):
        kwargs[self.instance_context_key] = self.object

        mode = self.request.GET.get('mode') or 'json'
        if mode not in ['json', 'inspect']:
            raise Http404

        snapshot1, snapshot2 = self.get_snapshots()
        if not snapshot1 or not snapshot2:
            return redirect(reverse(self.history_view, args=[self.object.pk]))
        if snapshot1.timestamp > snapshot2.timestamp:
            snapshot1, snapshot2 = snapshot2, snapshot1

        kwargs['mode'] = mode
        kwargs['snapshot1'] = snapshot1
        kwargs['snapshot2'] = snapshot2
        kwargs['fromdesc'] = self.get_snapshot_desc(snapshot1)
        kwargs['todesc'] = self.get_snapshot_desc(snapshot2)
        kwargs.update(getattr(self, 'get_compare_%s_context' % mode)(snapshot1, snapshot2))

        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        context = self.get_context_data(object=self.object)
        if isinstance(context, HttpResponseRedirect):
            return context
        return self.render_to_response(context)

    def get_compare_inspect_context(self, snapshot1, snapshot2):
        instance1 = snapshot1.get_instance()
        instance2 = snapshot2.get_instance()

        def get_context(instance):
            return {
                'object': instance,
            }

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
            return panel.html()

        inspect1 = loader.render_to_string(self.inspect_template_name, get_context(instance1), self.request)
        d1 = pq(str(inspect1))
        inspect2 = loader.render_to_string(self.inspect_template_name, get_context(instance2), self.request)
        d2 = pq(str(inspect2))
        panels_attrs = [tab.attrib for tab in d1('[role="tabpanel"]')]
        panels1 = list(d1('[role="tabpanel"]'))
        panels2 = list(d2('[role="tabpanel"]'))

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
            'tabs': tabs,
            'panels': zip(panels_attrs, panels_diff),
            'tab_class_names': d1('.pk-tabs').attr('class'),
        }

    def get_compare_json_context(self, snapshot1, snapshot2):
        s1 = json.dumps(snapshot1.serialization, sort_keys=True, indent=2)
        s2 = json.dumps(snapshot2.serialization, sort_keys=True, indent=2)
        diff_serialization = difflib.HtmlDiff(wrapcolumn=160).make_table(
            fromlines=s1.splitlines(True),
            tolines=s2.splitlines(True),
        )

        return {
            'diff_serialization': diff_serialization,
        }

    def get_snapshot_desc(self, snapshot):
        label_or_comment = ''
        if snapshot.label:
            label_or_comment = snapshot.label
        elif snapshot.comment:
            label_or_comment = snapshot.comment
        if snapshot.application_version:
            label_or_comment += ' (%s)' % _('Version %s') % snapshot.application_version
        return '{name} ({pk}) - {label_or_comment} ({user}{timestamp})'.format(
            name=_('Snapshot'),
            pk=snapshot.id,
            label_or_comment=label_or_comment,
            user='%s ' % snapshot.user if snapshot.user_id else '',
            timestamp=date_format(localtime(snapshot.timestamp), format='DATETIME_FORMAT'),
        )
