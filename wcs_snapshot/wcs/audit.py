# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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
from quixote import get_publisher, get_request

from wcs import sql
from wcs.qommon import _


class Audit(sql.Audit):
    id = None
    timestamp = None
    action = None
    url = None
    user_id = None
    object_type = None  # (formdef, carddef, etc.)
    object_id = None
    data_id = None  # (for formdata and carddata)
    extra_data = None

    @classmethod
    def record(cls, action, obj=None, user_id=None, **kwargs):
        audit = cls()
        audit.action = action
        audit.timestamp = now()
        request = get_request()
        user = None
        if user_id:
            audit.user_id = user_id
            user = get_publisher().user_class.get(audit.user_id, ignore_errors=True)
        elif request:
            user = request.get_user()
            if user and user.is_api_user:
                # do not audit API calls
                return
            if user and hasattr(user, 'id'):
                audit.user_id = user.id
        else:
            # try to get user from substitution variables when request doesn't exist,
            # for example because a delete workflow action is being run from a mass
            # action after job.
            context = get_publisher().substitutions.get_context_variables(mode='lazy')
            user_id = context.get('session_user_id')
            if user_id:
                audit.user_id = user_id
                user = get_publisher().user_class.get(audit.user_id, ignore_errors=True)
        if request:
            audit.url = request.get_path_query()
        object_natural_key = None
        if obj:
            if hasattr(obj, '_formdef'):  # formdata or carddata
                audit.data_id = obj.id
                object_natural_key = obj.get_natural_key()
                obj = obj._formdef
            audit.object_type = obj.xml_root_node
            audit.object_id = obj.id
        audit.extra_data = kwargs
        audit.frozen = {
            'user_email': getattr(user, 'email', None),
            'user_full_name': getattr(user, 'display_name', None),
            'user_nameid': getattr(user, 'nameid', None),
            'object_slug': getattr(obj, 'slug', None),
            'object_name': getattr(obj, 'name', None),
        }
        if object_natural_key:
            audit.frozen['object_natural_key'] = object_natural_key
        audit.store()

    @classmethod
    def get_action_labels(cls):
        return {
            'deletion': _('Deletion'),
            'listing': _('Listing'),
            'export.csv': _('CSV Export'),
            'export.ods': _('ODS Export'),
            'download file': _('Download of attached file'),
            'download files': _('Download of attached files (bundle)'),
            'redirect to remote stored file': _('Redirect to remote stored file'),
            'view': _('View Data'),
            'settings': _('Change to global settings'),
        }

    def get_action_description(self):
        action_label = self.get_action_labels().get(self.action, self.action)
        obj_name = self.frozen.get('object_name')
        if self.object_type and self.object_id:
            obj_class = get_publisher().get_object_class(self.object_type)
            obj = obj_class.get(self.object_id, ignore_errors=True)
            if obj:
                obj_name = obj.name
        parts = [str(action_label)]
        if obj_name:
            parts.append(obj_name)
        if self.frozen and self.frozen.get('object_natural_key'):
            parts.append(str(self.frozen.get('object_natural_key')))
        elif self.data_id:
            parts.append(str(self.data_id))
        if self.extra_data:
            if self.extra_data.get('extra_label'):
                parts.append(self.extra_data.get('extra_label'))
            elif self.extra_data.get('cfg_key'):
                parts.append(self.extra_data.get('cfg_key'))
        return ' - '.join(parts)

    @classmethod
    def clean(cls, publisher=None, **kwargs):
        audit_retention_days = (publisher or get_publisher()).get_site_option('audit-retention-days') or 365
        Audit.wipe(clause=[sql.Less('timestamp', now() - datetime.timedelta(days=int(audit_retention_days)))])
