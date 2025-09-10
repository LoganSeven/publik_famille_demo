# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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
import json
import sys
import time
import traceback
import uuid

from django.utils.encoding import force_str
from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_response, get_session
from quixote.directory import Directory

import wcs.sql

from . import N_, _, errors
from .storage import StorableObject
from .timings import TimingsMixin


class AbortJob(Exception):
    pass


class AfterJobStatusDirectory(Directory):
    def _q_lookup(self, component):
        get_request().ignore_session = True
        get_response().set_content_type('application/json')
        try:
            job = AfterJob.get(component)
        except KeyError:
            raise errors.TraversalError()

        if get_request().get_method() == 'POST':
            if get_request().form.get('action') == 'abort' and job.can_be_aborted():
                job.request_abort()
                return json.dumps({'err': 0})
            return json.dumps({'err': 1})

        if job.status == 'failed':
            message = job.failure_label or _('failed')
        else:
            message = _(job.status)
            completion_status = job.get_completion_status()
            if completion_status:
                message = f'{message} {completion_status}'
        return json.dumps(
            {
                'status': job.status,
                'message': str(message),
            }
        )


class AfterJob(wcs.sql.SqlAfterJob, TimingsMixin):
    _names = 'afterjobs'
    _reset_class = False

    label = None
    status = None
    creation_time = None
    completion_time = None
    current_count = None
    total_count = None
    failure_label = None
    user_id = None
    abort_requested = False

    _last_store_time = 0
    DELAY_BETWEEN_INCREMENT_STORES = 1

    execute = None

    def __init__(self, label=None, **kwargs):
        super().__init__(id=str(uuid.uuid4()))
        if label:
            self.label = force_str(label)
        self.done_action_label_arg = kwargs.pop('done_action_label', None)
        self.done_action_url_arg = kwargs.pop('done_action_url', None)
        self.done_button_attributes_arg = kwargs.pop('done_button_attributes', None)
        self.creation_time = localtime()
        self.status = N_('registered')
        self.kwargs = kwargs

    def __repr__(self):
        return f'<{self.__class__.__name__} id:{self.id}>'

    @property
    def typename(self):
        return self.__class__.__name__.removesuffix('Job').removesuffix('After')

    @classmethod
    def get(cls, id, *args, **kwargs):
        try:
            uuid.UUID(id)
        except ValueError:
            raise KeyError(id)
        return super().get(id, *args, **kwargs)

    def mark_as_failed(self, message=None):
        self.status = 'failed'
        if message:
            self.failure_label = str(message)
            self.store()

    def done_action_label(self):
        return self.done_action_label_arg

    def done_action_url(self):
        return self.done_action_url_arg % {'job_id': self.id} if self.done_action_url_arg else None

    def done_action_attributes(self):
        return self.done_button_attributes_arg

    def abort_similar(self):
        criterias = [
            wcs.sql.Equal('class_name', self.__class__.__name__),
            wcs.sql.Contains('status', ('registered', 'running')),
        ]
        if self.id and self.id is not self.DO_NOT_STORE:
            criterias.append(wcs.sql.NotEqual('id', self.id))
        for job in self.__class__.select(criterias):
            if job == self:
                job.request_abort()

    def increment_count(self, amount=1):
        self.current_count = (self.current_count or 0) + amount
        # delay storage to avoid repeated writes on slow storage
        if self.id and time.time() - self._last_store_time > self.DELAY_BETWEEN_INCREMENT_STORES:
            self.store_count()
            self.refresh_column('abort_requested')
            if self.abort_requested:
                raise AbortJob()

    def get_completion_status(self):
        current_count = self.current_count or 0

        if not current_count:
            return ''

        if not self.total_count:
            return _('%(current_count)s (unknown total)') % {'current_count': current_count}

        return _('%(current_count)s/%(total_count)s (%(percent)s%%)') % {
            'current_count': int(current_count),
            'total_count': self.total_count,
            'percent': int(current_count * 100 / self.total_count),
        }

    def can_be_aborted(self):
        session = get_session()
        session_user_id = session.get_user_id() if session else None
        if self.status not in ('registered', 'running') or not session_user_id:
            return False
        # allow users to abort their jobs, and allow admins to abort any job
        if self.user_id != session_user_id and not session.get_user().can_go_in_admin():
            return False
        return True

    def request_abort(self):
        self.abort_requested = True
        self.store_column('abort_requested')

    def run(self, *, publisher=None, spool=False):
        if self.completion_time:
            return

        if publisher is None:
            publisher = get_publisher()

        if spool and self.id and self.execute:
            from django.conf import settings

            if 'uwsgi' in sys.modules and settings.WCS_MANAGE_COMMAND:
                from .spooler import run_after_job

                self.store()
                run_after_job.spool(tenant_dir=publisher.app_dir, job_id=self.id)
                return

        self.status = N_('running')
        self.store_status()
        self.refresh_column('abort_requested')
        try:
            if self.abort_requested:
                raise AbortJob()
            self.execute()
        except AbortJob:
            self.status = N_('aborted')
        except Exception as e:
            if getattr(self, 'raise_exception', False):
                raise
            if self.status == 'running':
                # capture/record error unless it's been set already
                publisher.capture_exception(sys.exc_info())
                publisher.record_error(exception=e, record=False, notify=True)
                self.exception = traceback.format_exc()
                self.store()
                self.status = N_('failed')
        else:
            if self.status == 'running':
                self.status = N_('completed')
        self.completion_time = localtime()
        self.store_status()

    def __getstate__(self):
        if getattr(self, 'done_action_label_arg', None):
            self.done_action_label_arg = force_str(self.done_action_label_arg)
        obj_dict = self.__dict__.copy()
        if '_last_store_time' in obj_dict:
            del obj_dict['_last_store_time']
        return obj_dict

    @classmethod
    def clean(cls):
        from wcs.sql_criterias import And, Contains, Less, Or

        now = localtime()
        cls.wipe(
            clause=[
                Or(
                    [
                        # three days for completed/failed jobs
                        And(
                            [
                                Contains('status', ('completed', 'failed')),
                                Less('completion_time', now - datetime.timedelta(days=3)),
                            ]
                        ),
                        # five days for jobs that did not finish
                        Less('creation_time', now - datetime.timedelta(days=5)),
                    ]
                )
            ]
        )

    def get_api_status_url(self):
        return get_request().build_absolute_uri('/api/jobs/%s/' % self.id)

    def get_processing_url(self):
        return '/backoffice/processing?job=%s' % self.id


class FileAfterJob(StorableObject):
    # legacy class for migration
    _names = 'afterjobs'
    _reset_class = False
