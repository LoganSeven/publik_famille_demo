# w.c.s. - web application for online forms
# Copyright (C) 2005-2011  Entr'ouvert
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

from wcs.qommon import _, audit
from wcs.workflows import AbortOnRemovalException, WorkflowStatusItem, register_item_class


class RemoveWorkflowStatusItem(WorkflowStatusItem):
    description = _('Deletion')
    key = 'remove'
    category = 'formdata-action'

    def perform(self, formdata):
        audit('deletion', obj=formdata)
        formdata.remove_self()
        raise AbortOnRemovalException(formdata)


register_item_class(RemoveWorkflowStatusItem)
