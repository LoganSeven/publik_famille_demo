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

import time

from .qommon import sessions
from .qommon.sessions import Session


class BasicSession(Session):
    magictokens = None
    anonymous_formdata_keys = None
    visiting_objects = None
    latest_errors_visit = None

    def has_info(self):
        return (
            self.anonymous_formdata_keys
            or self.magictokens
            or self.visiting_objects
            or self.latest_errors_visit
            or Session.has_info(self)
        )

    is_dirty = has_info

    def add_magictoken(self, token, data):
        if not self.magictokens:
            self.magictokens = {}
        self.magictokens[token] = data

    def remove_magictoken(self, token):
        if not self.magictokens:
            return
        if token in self.magictokens:
            del self.magictokens[token]

    def mark_anonymous_formdata(self, formdata):
        if formdata and formdata.formdef.xml_root_node != 'formdef':
            return False
        if not self.anonymous_formdata_keys:
            self.anonymous_formdata_keys = {}
        key = formdata.get_object_key()
        if key not in self.anonymous_formdata_keys:
            self.anonymous_formdata_keys[key] = True
            return True
        return False

    def is_anonymous_submitter(self, formdata):
        if not self.anonymous_formdata_keys:
            return False
        return formdata.get_object_key() in self.anonymous_formdata_keys

    def mark_visited_object(self, formdata):
        key = formdata.get_object_key()
        if not self.visiting_objects:
            self.visiting_objects = {}
        # first clean older objects
        current_timestamp = time.time()
        for object_key, object_timestamp in list(self.visiting_objects.items()):
            if object_timestamp < (current_timestamp - 30 * 60):
                del self.visiting_objects[object_key]
        self.visiting_objects[key] = current_timestamp

    @classmethod
    def get_visited_objects(cls, exclude_user=None):
        # return the list of visited objects
        current_timestamp = time.time()
        visited_objects = {}
        for session in cls.select_recent_with_visits(ignore_errors=True):
            if session.user and session.user == exclude_user:
                continue
            visiting_objects = getattr(session, 'visiting_objects', None)
            if not visiting_objects:
                continue
            for object_key, object_timestamp in visiting_objects.items():
                if object_timestamp > (current_timestamp - 30 * 60):
                    visited_objects[object_key] = True
        return visited_objects.keys()

    @classmethod
    def get_object_visitors(cls, formdata):
        '''return tuples of (user_id, last_visit_timestamp)'''
        object_key = formdata.get_object_key()
        current_timestamp = time.time()
        visitors = {}
        for session in cls.get_sessions_with_visited_object(object_key):
            object_timestamp = session.visiting_objects.get(object_key)
            if object_timestamp > (current_timestamp - 30 * 60):
                visitors[session.user] = max(object_timestamp, visitors.get(session.user, 0))
        return visitors.items()

    def unmark_visited_object(self, formdata):
        object_key = formdata.get_object_key()
        # remove from current session
        if object_key in (getattr(self, 'visiting_objects', None) or {}):
            del self.visiting_objects[object_key]
        # and from others
        for session in self.__class__.select_recent_with_visits(ignore_errors=True):
            if session.id == self.id:
                continue
            visiting_objects = getattr(session, 'visiting_objects', None)
            if not visiting_objects:
                continue
            if object_key in visiting_objects:
                del session.visiting_objects[object_key]
                session.store()

    def set_user(self, user_id):
        super().set_user(user_id)
        if self.user:
            self.anonymous_formdata_keys = None
            self.magictokens = None


sessions.BasicSession = BasicSession
StorageSessionManager = sessions.StorageSessionManager
