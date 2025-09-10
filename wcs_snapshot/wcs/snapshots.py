# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

import difflib
import re
import xml.etree.ElementTree as ET

from django.utils.module_loading import import_string
from django.utils.timezone import now
from quixote import get_publisher, get_response, get_session

from wcs.qommon import _, misc
from wcs.sql_criterias import Equal


class UnknownUser:
    def __str__(self):
        return str(_('unknown user'))


_no_eol = '\\ No newline at end of file'
_hdr_pat = re.compile(r'^@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@$')


def make_patch(a, b):
    """
    Get unified string diff between two strings. Trims top two lines.
    Returns empty string if strings are identical.
    """
    diffs = difflib.unified_diff(a.splitlines(True), b.splitlines(True), n=0)
    try:
        _, _ = next(diffs), next(diffs)
    except StopIteration:
        pass
    return ''.join([d if d[-1] == '\n' else d + '\n' + _no_eol + '\n' for d in diffs])


def apply_patch(s, patch, revert=False):
    """
    Apply patch to string s to recover newer string.
    If revert is True, treat s as the newer string, recover older string.
    """
    s = s.splitlines(True)
    p = patch.splitlines(True)
    t = ''
    i = sl = 0
    (midx, sign) = (1, '+') if not revert else (3, '-')
    while i < len(p) and p[i].startswith(('---', '+++')):
        i += 1  # skip header lines
    while i < len(p):
        m = _hdr_pat.match(p[i])
        if not m:
            raise Exception('Bad patch -- regex mismatch [line ' + str(i) + ']')
        _l = int(m.group(midx)) - 1 + (m.group(midx + 1) == '0')
        if sl > _l or _l > len(s):
            raise Exception('Bad patch -- bad line num [line ' + str(i) + ']')
        t += ''.join(s[sl:_l])
        sl = _l
        i += 1
        while i < len(p) and p[i][0] != '@':
            if i + 1 < len(p) and p[i + 1][0] == '\\':
                line = p[i][:-1]
                i += 2
            else:
                line = p[i]
                i += 1
            if len(line) > 0:
                if line[0] == sign or line[0] == ' ':
                    t += line[1:]
                sl += line[0] != sign
    t += ''.join(s[sl:])
    return t


class Snapshot:
    '''
    Snapshot of an object, used to provide an history of changes, diffs between
    versions, etc.

    It is stored either as a full serialization (in the serialization attribute)
    or as a patch against the latest full serialization (in the patch attribute).
    '''

    id = None
    object_type = None  # (formdef, carddef, blockdef, workflow, data_source, etc.)
    object_id = None
    timestamp = None
    user_id = None
    comment = None
    serialization = None
    patch = None
    label = None  # (named snapshot)
    test_results_id = None
    deleted_object = False

    application_slug = None
    application_version = None
    # ignore this snapshot when comparing application objects
    application_ignore_change = False

    # cache
    _instance = None
    _safe_instance = None
    _user = None

    _object_types = [
        'block',
        'carddef',
        'comment-template',
        'mail-template',
        'datasource',
        'formdef',
        'testdef',
        'user',
        'workflow',
        'wscall',
    ]
    _category_types = [
        'block_category',
        'carddef_category',
        'data_source_category',
        'category',
        'mail_template_category',
        'comment_template_category',
        'workflow_category',
    ]

    @classmethod
    def snap(
        cls,
        instance,
        comment=None,
        label=None,
        store_user=None,
        force_full_store=False,
        application=None,
        application_ignore_change=False,
    ):
        obj = cls()
        obj.object_type = instance.xml_root_node
        assert obj.object_type in (cls._object_types + cls._category_types)
        obj.object_id = instance.id
        obj.timestamp = now()
        # store_user:
        #  None/True: get user from active session
        #  False: do not store user
        #  any value: consider it as user id
        # (store_user is explicitely checked to be a boolean, to avoid the "1" integer being treated as True)
        if store_user is None or (isinstance(store_user, bool) and store_user is True):
            if get_session():
                obj.user_id = get_session().user
        elif store_user:
            obj.user_id = store_user

        tree = instance.export_to_xml(include_id=True)
        # remove position for categories
        if obj.object_type in cls._category_types:
            for position in tree.findall('position'):
                tree.remove(position)

        obj.serialization = ET.tostring(tree).decode('utf-8')
        obj.comment = str(comment) if comment else None
        obj.label = label
        if application is not None:
            obj.application_slug = application.slug
            obj.application_version = application.version_number
        obj.application_ignore_change = application_ignore_change

        latest_complete = cls.get_latest(obj.object_type, obj.object_id, complete=True)
        if latest_complete is None or force_full_store:
            # no complete snapshot, store it, with serialization and no patch
            obj.store()
            return

        # should we store a snapshot ?
        store_snapshot = True

        # get patch between latest serialization and current instance
        # indent xml to minimize patch
        try:
            latest_tree = ET.fromstring(latest_complete.serialization)
        except ET.ParseError:
            patch = None
        else:
            ET.indent(tree)
            ET.indent(latest_tree)
            patch = make_patch(ET.tostring(latest_tree).decode('utf-8'), ET.tostring(tree).decode('utf-8'))
            if label is None:
                # compare with patch of latest snapshot
                latest = cls.get_latest(obj.object_type, obj.object_id)
                if latest.patch and patch == latest.patch:
                    # previous snapshot contains a patch (but no serialization)
                    # and the current patch is the same as in the previous snapshot
                    store_snapshot = False
                elif latest.serialization and not patch:
                    # previous snapshot contains a serialization (but no patch)
                    # and there is no difference (no patch)
                    store_snapshot = False

        if application is not None:
            # always store a snapshot on application import, we want to have a trace in history
            store_snapshot = True

        if store_snapshot:
            if patch is not None and len(patch) < (len(obj.serialization) / 10):
                # patch is small (compared to full serialization)
                # -> store patch instead of full serialization
                obj.serialization = None
                obj.patch = patch
            # else: keep serialization and ignore patch
            obj.store()

            if get_response() and obj.object_type in ('formdef', 'carddef'):
                from wcs.admin.tests import TestsAfterJob

                get_publisher().add_after_job(
                    TestsAfterJob(instance, reason=obj.label or obj.comment, snapshot=obj)
                )

    @classmethod
    def snap_deletion(cls, instance):
        cls.snap(instance=instance, comment=_('Deletion'), force_full_store=True)
        cls.mark_deleted_object(instance.xml_root_node, str(instance.id))

    @classmethod
    def get_recent_changes(cls, object_types=None, user=None, limit=5, offset=0):
        elements = cls._get_recent_changes(object_types=object_types, user=user, limit=limit, offset=offset)
        instances = []

        for object_type, object_id, snapshot_timestamp in elements:
            klass = cls.get_class(object_type)
            instance = klass.get(object_id, ignore_errors=True)
            if instance:
                instance.snapshot_timestamp = snapshot_timestamp
                instances.append(instance)
            else:
                instances.append(None)
        return instances

    def get_object_class(self):
        return self.get_class(self.object_type)

    @classmethod
    def get_class(cls, object_type):
        if object_type == 'user':
            from wcs.sql import TestUser

            return TestUser
        return get_publisher().get_object_class(object_type)

    def get_serialization(self, indented=True):
        # there is a complete serialization
        if self.serialization:
            if not indented:
                return self.serialization

            tree = ET.fromstring(self.serialization)
            ET.indent(tree)
            return ET.tostring(tree).decode('utf-8')

        # get latest version with serialization
        latest_complete = self.__class__.get_latest(
            self.object_type,
            self.object_id,
            complete=True,
            max_timestamp=self.timestamp,
            include_deleted=True,
        )
        latest_tree = ET.fromstring(latest_complete.serialization)
        ET.indent(latest_tree)
        serialization = apply_patch(ET.tostring(latest_tree).decode('utf-8'), self.patch or '')
        return serialization

    @property
    def instance(self):
        if self._instance is None:
            tree = ET.fromstring(self.get_serialization())
            self._instance = self.get_object_class().import_from_xml_tree(
                tree,
                include_id=True,
                snapshot=True,
                check_datasources=getattr(self, '_check_datasources', True),
                check_deprecated=False,
            )
            self._instance.readonly = True
            self._instance.snapshot_object = self
        return self._instance

    @property
    def safe_instance(self):
        if self._safe_instance is None:
            tree = ET.fromstring(self.get_serialization())
            self._safe_instance = self.get_object_class().import_from_xml_tree(
                tree,
                include_id=True,
                snapshot=True,
                check_datasources=False,
                check_deprecated=False,
                ignore_missing_dependencies=True,
            )
            self._safe_instance.readonly = True
            self._safe_instance.snapshot_object = self
        return self._safe_instance

    @property
    def user(self):
        if not self.user_id:
            return None
        if self._user is None:
            try:
                self._user = get_publisher().user_class.get(self.user_id)
            except KeyError:
                self._user = UnknownUser()
        return self._user

    def load_history(self):
        if not self.instance:
            self._history = []
            return
        history = get_publisher().snapshot_class.select_object_history(self.instance)
        self._history = [s.id for s in history]

    @property
    def previous(self):
        if not hasattr(self, '_history'):
            self.load_history()

        try:
            idx = self._history.index(self.id)
        except ValueError:
            return None
        if idx == 0:
            return None
        return self._history[idx - 1]

    @property
    def next(self):
        if not hasattr(self, '_history'):
            self.load_history()

        try:
            idx = self._history.index(self.id)
        except ValueError:
            return None
        try:
            return self._history[idx + 1]
        except IndexError:
            return None

    @property
    def first(self):
        if not hasattr(self, '_history'):
            self.load_history()

        return self._history[0]

    @property
    def last(self):
        if not hasattr(self, '_history'):
            self.load_history()

        return self._history[-1]

    def restore(self, as_new=True):
        instance = self.instance
        if as_new:
            for attr in ('id', 'url_name', 'internal_identifier', 'slug'):
                try:
                    setattr(instance, attr, None)
                except AttributeError:
                    # attribute can be a property without setter
                    pass
            if self.object_type in self._category_types:
                # set position
                instance.position = max(i.position or 0 for i in self.get_object_class().select()) + 1
            elif self.object_type == 'testdef':
                instance.workflow_tests.id = None
                for response in instance.get_webservice_responses():
                    response.id = None
            if hasattr(instance, 'disabled'):
                instance.disabled = True
        else:
            # keep table and position from current object
            current_object = self.get_object_class().get(instance.id, ignore_errors=True)
            if current_object:
                for attr in ('table_name', 'position'):
                    if attr != 'position' or self.object_type in self._category_types:
                        if hasattr(current_object, attr):
                            setattr(instance, attr, getattr(current_object, attr))
            self.unmark_deleted_object(instance.xml_root_node, str(instance.id))

        delattr(instance, 'readonly')
        delattr(instance, 'snapshot_object')
        instance.store(
            comment=_('Restored snapshot %(id)s (%(timestamp)s)')
            % {'id': self.id, 'timestamp': misc.localstrftime(self.timestamp)}
        )
        return instance

    def can_restore(self):
        # restore is allowed if object con be edited by user
        if getattr(self.safe_instance, 'category_class', None):
            # check access when it's given by category
            category_class = import_string(self.safe_instance.category_class)
            if category_class.has_global_access():
                return True
            if self.safe_instance.category and self.safe_instance.category.is_managed_by_user():
                return True
            return False
        # check access when it's managed globally
        # (objects without categories: webservice calls and categories)
        return self.safe_instance.has_admin_access(get_session().user)

    @classmethod
    def clean(cls, publisher=None, **kwargs):
        snapshot_class = get_publisher().snapshot_class
        # mark deleted objects
        existing_objects = []
        for object_type in cls._object_types + cls._category_types:
            kls = cls.get_class(object_type)
            existing_objects.extend([(object_type, str(x)) for x in kls.keys()])
        snapshot_class.mark_deleted_objects(existing_objects)

        # keep a single snapshot for objects that have been deleted for some time
        for object_type in cls._object_types + cls._category_types:
            criterias = [Equal('object_type', object_type)]
            for dummy, object_id, count in snapshot_class.select_old_objects_and_count(
                criterias, include_retention=True
            ):
                if count > 1:
                    snapshot_class.delete_all_but_latest(object_type, object_id)

    @classmethod
    def get_deleted_items(cls, *, more_criterias=None):
        snapshot_class = get_publisher().snapshot_class
        for object_type in cls._object_types + cls._category_types:
            criterias = [Equal('object_type', object_type)]
            criterias.extend(more_criterias or [])
            # get ids of objects that have been deleted
            for object_type, object_id, dummy in snapshot_class.select_old_objects_and_count(criterias):
                yield (object_type, object_id)
