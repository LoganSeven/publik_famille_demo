# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

import configparser
import copy
import threading

import django.apps
from django.conf import settings
from quixote import get_publisher

from .publisher import get_publisher_class


class TenantAwareThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        self.publisher = copy.copy(get_publisher())
        if self.publisher:
            self.publisher.detach()
        super().__init__(*args, **kwargs)

    def run(self):
        if self.publisher:
            self.publisher.set_in_thread()
        super().run()


class _Timer(TenantAwareThread):
    """Call a function after a specified number of seconds:

    t = Timer(30.0, f, args=[], kwargs={})
    t.start()
    t.cancel()     # stop the timer's action if it's still waiting

    """

    def __init__(self, interval, function, args=None, kwargs=None):
        super().__init__()
        self.interval = interval
        self.function = function
        self.args = args or []
        self.kwargs = kwargs or {}
        self.finished = threading.Event()

    def cancel(self):
        """Stop the timer if it hasn't finished yet"""
        self.finished.set()

    def run(self):
        self.finished.wait(self.interval)
        if not self.finished.is_set():
            self.function(*self.args, **self.kwargs)
        self.finished.set()


class _MainThread(TenantAwareThread):
    def __init__(self):
        super().__init__(name='MainThread')
        self._Thread__started.set()
        self._set_ident()
        with threading._active_limbo_lock:
            threading._active[threading._get_ident()] = self

    def _set_daemon(self):
        return False

    def _exitfunc(self):
        self._Thread__stop()
        t = threading._pickSomeNonDaemonThread()
        if t:
            if __debug__:
                self._note('%s: waiting for other threads', self)
        while t:
            t.join()
            t = threading._pickSomeNonDaemonThread()
        if __debug__:
            self._note('%s: exiting', self)
        self._Thread__delete()


class _DummyThread(TenantAwareThread):
    def __init__(self):
        super().__init__(name=threading._newname('Dummy-%d'), daemon=True)

        self._started.set()
        self._set_ident()
        with threading._active_limbo_lock:
            threading._active[self._ident] = self

    def _stop(self):
        pass

    def is_alive(self):
        assert not self._is_stopped and self._started.is_set()
        return True

    def join(self, timeout=None):
        assert False, 'cannot join a dummy thread'


class AppConfig(django.apps.AppConfig):
    name = 'wcs.qommon'

    def ready(self):
        config = configparser.ConfigParser()
        if settings.WCS_LEGACY_CONFIG_FILE:
            config.read(settings.WCS_LEGACY_CONFIG_FILE)

        threading.Thread = TenantAwareThread
        threading._DummyThread = _DummyThread
        threading._MainThread = _MainThread
        threading._Timer = _Timer

        get_publisher_class().configure(config)
        get_publisher_class().register_tld_names = True
        get_publisher_class().init_publisher_class()
