# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
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


import contextlib
import datetime
import logging
import os
import pickle
import shutil
import threading

from atomicwrites import AtomicWriter
from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connection
from django.utils.functional import cached_property
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from authentic2.utils.crypto import new_base64url_id
from authentic2.utils.misc import gettid

logger = logging.getLogger(__name__)


class UserImport:
    def __init__(self, uuid, user=None):
        self.uuid = uuid
        self.path = os.path.join(self.base_path(), self.uuid)
        self.import_path = os.path.join(self.path, 'content')
        self.meta_path = os.path.join(self.path, 'meta.pck')

    def exists(self):
        return os.path.exists(self.import_path) and os.path.exists(self.meta_path)

    @cached_property
    def created(self):
        return datetime.datetime.fromtimestamp(os.path.getctime(self.path), datetime.UTC)

    @property
    def import_file(self):
        return open(self.import_path, 'rb')

    @cached_property
    def meta(self):
        meta = {}
        if os.path.exists(self.meta_path):
            with open(self.meta_path, 'rb') as fd:
                meta = pickle.load(fd)
        return meta

    @property
    @contextlib.contextmanager
    def meta_update(self):
        try:
            yield self.meta
        finally:
            with AtomicWriter(self.meta_path, mode='wb', overwrite=True).open() as fd:
                pickle.dump(self.meta, fd)

    @classmethod
    def base_path(cls):
        path = default_storage.path('user_imports')
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    @classmethod
    def new(cls, import_file, encoding):
        o = cls(new_base64url_id())
        os.makedirs(o.path)
        with open(o.import_path, 'wb') as fd:
            import_file.seek(0)
            fd.write(import_file.read())
        with o.meta_update as meta:
            meta['encoding'] = encoding
        return o

    def new_report(self):
        return Report.new(self)

    @classmethod
    def all(cls):
        for subpath in os.listdir(cls.base_path()):
            user_import = UserImport(subpath)
            if user_import.exists():
                yield user_import

    @property
    def reports(self):
        return Reports(self)

    def __getattr__(self, name):
        try:
            return self.meta[name]
        except KeyError:
            raise AttributeError(name)

    def delete(self):
        if self.exists():
            shutil.rmtree(self.path)


@contextlib.contextmanager
def _report_publik_provisionning(simulate=False):
    if 'hobo.agent.authentic2' in settings.INSTALLED_APPS and not simulate:
        # provisionning is initialied in hobo.agent.authentic2.provisionning.apps
        # pylint: disable=import-error
        from hobo.agent.authentic2.provisionning import provisionning as engine

        with engine:
            yield None
    else:
        yield None


class Report:
    STATE_WAITING = 'waiting'
    STATE_RUNNING = 'running'
    STATE_FINISHED = 'finished'
    STATE_ERROR = 'error'
    STATES = {
        STATE_WAITING: _('Waiting'),
        STATE_RUNNING: _('Running'),
        STATE_FINISHED: _('Finished'),
        STATE_ERROR: _('Error'),
    }

    def __init__(self, user_import, uuid):
        self.user_import = user_import
        self.uuid = uuid
        self.path = os.path.join(self.user_import.path, '%s%s' % (Reports.PREFIX, uuid))

    @cached_property
    def created(self):
        return datetime.datetime.fromtimestamp(os.path.getctime(self.path), datetime.UTC)

    @cached_property
    def data(self):
        data = {}
        if os.path.exists(self.path):
            with open(self.path, 'rb') as fd:
                data = pickle.load(fd)
        return data

    @property
    def state(self):
        state = self.data['state']
        if state == self.STATE_RUNNING and not self.is_running:
            state = self.STATE_ERROR
        return state

    @property
    def is_running(self):
        try:
            pid = self.pid
            tid = self.tid
            return os.path.exists('/proc/%s/task/%s/' % (pid, tid))
        except AttributeError:
            return False

    @property
    def state_display(self):
        state = self.data['state']
        state_display = self.STATES.get(state, state)
        if state == self.STATE_RUNNING and 'progress' in self.data:
            state_display = '%s (%s)' % (state_display, self.data['progress'])
        return state_display

    @property
    @contextlib.contextmanager
    def data_update(self):
        try:
            yield self.data
        finally:
            with AtomicWriter(self.path, mode='wb', overwrite=True).open() as fd:
                pickle.dump(self.data, fd)

    @classmethod
    def new(cls, user_import):
        report = cls(user_import, new_base64url_id())
        with report.data_update as data:
            data['encoding'] = user_import.meta['encoding']
            data['ou'] = user_import.meta.get('ou')
            data['state'] = cls.STATE_WAITING
        return report

    def run(self, start=True, simulate=False, user=None):
        assert self.data.get('state') == self.STATE_WAITING

        with self.data_update as data:
            data['simulate'] = simulate
            if user is not None:
                data['user'] = user.get_full_name()
                data['user_pk'] = user.pk

        def callback(status, line, total):
            if total < 1 or not self.exists():
                return
            with self.data_update as data:
                data['progress'] = '%s, %d%%' % (status, round((line / total) * 100))

        def _run():
            from authentic2.apps.journal.journal import Journal
            from authentic2.csv_import import UserCsvImporter

            with self.user_import.import_file as fd:
                importer = UserCsvImporter(
                    user=user, user_import_uuid=self.user_import.uuid, report_uuid=self.uuid
                )
                start = datetime.datetime.now()
                with self.data_update as data:
                    data['state'] = self.STATE_RUNNING
                    data['pid'] = os.getpid()
                    data['tid'] = gettid()
                try:
                    with _report_publik_provisionning(simulate):
                        importer.run(
                            fd,
                            encoding=self.data['encoding'],
                            ou=self.data['ou'],
                            simulate=simulate,
                            progress_callback=callback,
                        )
                except Exception as e:
                    logger.exception('error during report %s:%s run', self.user_import.uuid, self.uuid)
                    state = self.STATE_ERROR
                    try:
                        exception = str(e)
                    except Exception:
                        exception = repr(repr(e))
                    Journal(user=user).record(
                        'manager.user.csvimport.run',
                        action_name=gettext('import error'),
                        import_uuid=self.user_import.uuid,
                        report_uuid=self.uuid,
                    )
                else:
                    exception = None
                    state = self.STATE_FINISHED
                finally:
                    duration = datetime.datetime.now() - start
                    try:
                        connection.close()
                    except Exception:
                        logger.exception('cannot close connection to DB')

                with self.data_update as data:
                    data['state'] = state
                    data['exception'] = exception
                    data['importer'] = importer
                    data['duration'] = duration

        def thread_worker():
            with contextlib.suppress(FileNotFoundError):
                _run()

        t = threading.Thread(target=thread_worker)
        t.daemon = True
        if start:
            t.start()
        return t

    def __getattr__(self, name):
        try:
            return self.data[name]
        except KeyError:
            raise AttributeError(name)

    def exists(self):
        return os.path.exists(self.path)

    def delete(self):
        if self.simulate and self.exists():
            os.unlink(self.path)


class Reports:
    PREFIX = 'report-'

    def __init__(self, user_import):
        self.user_import = user_import

    def __getitem__(self, uuid):
        report = Report(self.user_import, uuid)
        if not report.exists():
            raise KeyError
        return report

    def __iter__(self):
        for name in os.listdir(self.user_import.path):
            if name.startswith(self.PREFIX):
                try:
                    yield self[name[len(self.PREFIX) :]]
                except KeyError:
                    pass
