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

import contextlib
import time


class TimingsMixin:
    timings = None

    def start_timing(self, name):
        if not self.timings:
            self.timings = []
        self.timings.append({'name': name, 'start': time.time()})
        return self.timings[-1]

    def stop_timing(self, timing):
        timing['end'] = time.time()
        timing['duration'] = timing['end'] - timing['start']
        self.close_last_mark(timing['end'])
        return timing['duration']

    def close_last_mark(self, timestamp):
        timings = self.timings[-1].setdefault('timings', [])
        if timings and not timings[-1].get('duration'):
            timings[-1]['duration'] = timestamp - (timings[-1].get('timestamp') or timings[-1].get('start'))

    def add_timing_mark(self, name, relative_start=None, **context):
        if not self.timings:
            return
        name = str(name)
        # current group timings
        timestamp = time.time()
        self.close_last_mark(timestamp)
        record = {'mark': name, 'timestamp': timestamp}
        if context:
            record['context'] = context
        if relative_start:
            record['duration'] = timestamp - relative_start
        timings = self.timings[-1].setdefault('timings', [])
        timings.append(record)
        return record

    @contextlib.contextmanager
    def add_timing_group(self, name, **context):
        if not self.timings:
            yield
            return
        record = self.add_timing_mark(name, **context)
        self.timings.append(record)
        try:
            yield record
        finally:
            timestamp = time.time()
            record['duration'] = timestamp - record['timestamp']
            self.close_last_mark(timestamp)
            self.timings.pop()
