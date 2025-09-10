# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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
import re

from django.utils.formats import number_format
from django.utils.timezone import now
from quixote import get_publisher
from quixote.html import htmlescape, htmltext

import wcs.sql
from wcs import sql_criterias

from .qommon.misc import simplify


class LoggedError(wcs.sql.LoggedError):
    _names = 'logged-errors'

    id = None
    kind = None
    tech_id = None
    summary = None
    formdef_class = None
    formdata_id = None
    formdef_id = None
    workflow_id = None
    status_id = None
    status_item_id = None
    expression = None
    expression_type = None
    context = None
    traceback = None
    exception_class = None
    exception_message = None
    occurences_count = 0
    first_occurence_timestamp = None
    latest_occurence_timestamp = None
    deleted_timestamp = None
    documentation = None  # more like notes

    @classmethod
    def record(
        cls,
        error_summary,
        plain_error_msg=None,
        *,
        formdata=None,
        formdef=None,
        workflow=None,
        status=None,
        status_item=None,
        expression=None,
        expression_type=None,
        exception=None,
        kind=None,
        extra_context=None,
    ):
        # noqa pylint: disable=too-many-arguments
        error = cls()
        error.kind = kind
        error.summary = error_summary
        error.traceback = plain_error_msg
        error.expression = expression
        error.expression_type = expression_type

        if exception:
            error.exception_class = exception.__class__.__name__
            error.exception_message = str(exception)

        if formdata:
            error.formdata_id = str(formdata.id)
            formdef = formdata.formdef
        if formdef:
            error.formdef_id = formdef.id
            error.workflow_id = formdef.workflow.id
            error.formdef_class = formdef.__class__.__name__
        elif workflow:
            error.workflow_id = workflow.id

        if status_item:
            error.status_item_id = status_item.id
            if getattr(status_item, 'parent', None):
                error.status_id = status_item.parent.id
        if status:
            error.status_id = status.id

        error.context = get_publisher().get_error_context()
        if extra_context:
            error.context = (error.context or {}) | extra_context

        error.first_occurence_timestamp = now()
        error.tech_id = error.build_tech_id()
        error.occurences_count += 1
        error.latest_occurence_timestamp = now()
        return error.store()

    def record_new_occurence(self, error):
        if not self.id:
            return
        self.occurences_count += 1
        self.kind = error.kind
        self.latest_occurence_timestamp = now()
        self.deleted_timestamp = None
        # update with new error context
        self.formdata_id = error.formdata_id
        self.summary = error.summary
        self.traceback = error.traceback
        self.expression = error.expression
        self.expression_type = error.expression_type
        self.context = error.context
        # exception should be the same (same tech_id), record just in case
        self.exception_class = error.exception_class
        self.exception_message = error.exception_message
        return self.store()

    @classmethod
    def record_error(cls, error_summary, plain_error_msg, publisher, kind=None, *args, **kwargs):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        formdef = kwargs.pop('formdef', None)
        formdata = kwargs.pop('formdata', None)
        workflow = kwargs.pop('workflow', None)
        if not any([formdef, formdata, workflow]):
            try:
                context = publisher.substitutions.get_context_variables(mode='lazy')
            except Exception:
                return
            formdata_id = context.get('form_number_raw')
            formdef_urlname = context.get('form_slug')
            formdef_classname = context.get('form_class_name')
            if formdef_urlname and formdef_classname:
                klass = FormDef
                if formdef_classname == 'CardDef':
                    klass = CardDef
                formdef = klass.get_by_urlname(formdef_urlname)
                formdata = formdef.data_class().get(formdata_id, ignore_errors=True)
                workflow = formdef.workflow
            else:
                formdef = formdata = workflow = None
        return cls.record(
            error_summary,
            plain_error_msg,
            formdata=formdata,
            formdef=formdef,
            workflow=workflow,
            kind=kind,
            *args,
            **kwargs,
        )

    def build_tech_id(self):
        tech_id = ''
        if self.formdef_id:
            tech_id += '%s-' % self.formdef_id
        tech_id += '%s-' % self.workflow_id
        if self.status_id:
            tech_id += '%s-' % self.status_id
        if self.status_item_id:
            tech_id += '%s-' % self.status_item_id
        tech_id += '%s' % simplify(re.sub(r'\d', '', self.summary))
        if self.exception_class:
            tech_id += '-%s' % self.exception_class
        if self.exception_message:
            tech_id += '-%s' % simplify(re.sub(r'\d', '', self.exception_message))
        return tech_id[:200]

    def get_formdef(self):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        if self.formdef_class == 'CardDef':
            return CardDef.get(self.formdef_id, ignore_errors=True)
        return FormDef.get(self.formdef_id, ignore_errors=True)

    def get_workflow(self):
        from wcs.workflows import Workflow

        return Workflow.get(self.workflow_id, ignore_errors=True)

    def get_formdata(self):
        if not self.formdata_id:
            return None
        formdef = self.get_formdef()
        if not formdef:
            return None
        return formdef.data_class().get(self.formdata_id, ignore_errors=True)

    def get_status(self):
        if not self.status_id:
            return None
        workflow = self.get_workflow()
        if not workflow:
            return None
        for status in workflow.possible_status:
            if status.id == self.status_id:
                return status
        return None

    def get_status_item(self):
        status = self.get_status()
        if not status or not status.items:
            return None
        for status_item in status.items:
            if status_item.id == self.status_item_id:
                return status_item
        return None

    @property
    def formatted_occurences_count(self):
        return number_format(self.occurences_count, force_grouping=True)

    @property
    def wbr_summary(self):
        # allow word breaking before parenthesis, dots, underscores, and between CamelCasedWords
        return htmltext(re.sub(r'([\(\._]|[A-Z][a-z])', r'<wbr/>\1', str(htmlescape(self.summary))))

    @staticmethod
    def clean(publisher=None, **kwargs):
        # remove for real deleted errors after 30 days
        LoggedError.wipe(
            clause=[sql_criterias.Less('deleted_timestamp', now() - datetime.timedelta(days=30))]
        )

    @classmethod
    def get_permission_criterias(cls):
        # check permissions, exclude errors related to not accessible items
        clauses = []
        backoffice_root = get_publisher().get_backoffice_root()
        if not backoffice_root.is_accessible('forms'):
            clauses.append(sql_criterias.NotEqual('formdef_class', 'FormDef'))
        if not backoffice_root.is_accessible('cards'):
            clauses.append(sql_criterias.NotEqual('formdef_class', 'CardDef'))
        if not backoffice_root.is_accessible('workflows'):
            # exclude workflow-only errors
            clauses.append(sql_criterias.NotNull('formdef_class'))
        return clauses
