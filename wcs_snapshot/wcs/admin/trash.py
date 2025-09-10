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

import datetime

from django.utils.timezone import now
from quixote import get_publisher, get_response, redirect
from quixote.directory import AccessControlled, Directory
from quixote.html import TemplateIO, htmltext

from wcs.blocks import BlockdefImportError
from wcs.formdef_base import FormdefImportError
from wcs.qommon import _, errors, template
from wcs.qommon.form import Form
from wcs.sql_criterias import GreaterOrEqual
from wcs.workflows import WorkflowImportError


class TrashDirectory(AccessControlled, Directory):
    _q_exports = ['']
    do_not_call_in_templates = True

    def is_accessible(self):
        backoffice_root = get_publisher().get_backoffice_root()
        for section in ('forms', 'workflows', 'cards'):
            if backoffice_root.is_global_accessible(section):
                return True
        return False

    def _q_access(self):
        if not self.is_accessible():
            raise errors.AccessForbiddenError()

    def _q_index(self):
        get_response().breadcrumb.append(('trash/', _('Trash')))
        get_response().set_title(_('Trash, recently deleted items'))
        get_response().add_javascript(['popup.js'])

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/trash.html'],
            context={
                'view': self,
            },
            is_django_native=True,
        )

    def get_snapshots_of_deleted_elements(self):
        snapshots = [
            get_publisher().snapshot_class.get_latest(object_type, object_id, include_deleted=True)
            for object_type, object_id in get_publisher().snapshot_class.get_deleted_items(
                more_criterias=[GreaterOrEqual('timestamp', now() - datetime.timedelta(days=30))]
            )
            if object_type not in ('testdef', 'user')
        ]
        snapshots.sort(key=lambda x: x.timestamp, reverse=True)
        return snapshots

    def _q_lookup(self, component):
        snapshot = get_publisher().snapshot_class.get(component)
        if snapshot.get_object_class().has_key(snapshot.object_id):
            raise errors.TraversalError(_('An object with this id already exists.'))
        form = Form(enctype='multipart/form-data')
        form.add_submit('submit', _('Restore'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted():
            try:
                instance = snapshot.restore(as_new=False)
            except (BlockdefImportError, FormdefImportError, WorkflowImportError) as e:
                reason = _(e.msg) % e.msg_args
                if e.details:
                    reason += ' [%s]' % e.details
                error_msg = _('Can not restore snapshot (%s)') % reason
                form.add_global_errors([error_msg])
            else:
                return redirect(instance.get_admin_url())

        get_response().breadcrumb.append((component, _('Restore #%s') % component))
        get_response().set_title(_('Restore'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s - %s</h2>') % (_('Restore'), snapshot.safe_instance.name)
        r += form.render()
        return r.getvalue()
