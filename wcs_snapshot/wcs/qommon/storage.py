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

import builtins
import copy
import copyreg
import operator
import os
import os.path
import pickle
import shutil
import sys
import tempfile
import time

from django.utils.decorators import classonlymethod
from django.utils.encoding import force_bytes
from quixote import get_publisher

# add compatibility names in case those were stored in pickles
sys.modules['copy_reg'] = copyreg
sys.modules['__builtin__'] = builtins


def cache_umask():
    global process_umask
    process_umask = os.umask(0)
    os.umask(process_umask)


# cache umask when loading up the module
cache_umask()


def _take(objects, limit, offset=0):
    for y in objects:
        if offset:
            offset -= 1
            continue
        if limit:
            limit -= 1
        elif limit == 0:
            break
        elif limit is None:
            pass
        yield y


def lax_int(s):
    try:
        return int(s)
    except ValueError:
        return -1


def fix_key(k):
    # insure key can be inserted in filesystem
    if not k:
        return k
    return str(k).replace('/', '-')


def atomic_write(path, content):
    """Rewrite a complete file automatically, that is write to new file with
    temporary name, fsync, then rename to final name."""

    dirname = os.path.dirname(path)
    fd, temp = tempfile.mkstemp(dir=dirname, prefix='.tmp-' + os.path.basename(path) + '-')
    os.fchmod(fd, 0o666 & ~process_umask)
    f = os.fdopen(fd, 'wb')
    if hasattr(content, 'read'):
        # file pointer
        def read100k():
            return content.read(100000)

        for piece in iter(read100k, b''):
            f.write(piece)
    else:
        f.write(content)
    f.flush()
    os.fsync(f.fileno())
    f.close()
    os.rename(temp, path)


class Criteria:
    def __init__(self, attribute, value, **kwargs):
        self.attribute = attribute
        self.value = value
        # Python 3 requires comparisons to disparate types, this means we need
        # to create a null value of the appropriate type, so None values can
        # still be sorted.
        self.typed_none = ''
        if isinstance(self.value, bool):
            self.typed_none = False
        elif isinstance(self.value, (int, float)):
            self.typed_none = -sys.maxsize
        elif isinstance(self.value, time.struct_time):
            self.typed_none = time.gmtime(-(10**10))  # 1653

    def build_lambda(self):
        def func(x):
            attribute = getattr(x, self.attribute, None)
            if isinstance(self.value, int):
                try:
                    attribute = int(attribute)
                except TypeError:
                    pass
            return self.op(attribute or self.typed_none, self.value)

        return func

    def __repr__(self):
        return '<%s (attribute: %r%s)>' % (
            self.__class__.__name__,
            getattr(self, 'attribute', None),
            ', value: %s' % repr(self.value) if hasattr(self, 'value') else '',
        )


class Less(Criteria):
    op = operator.lt


class Greater(Criteria):
    op = operator.gt


class Equal(Criteria):
    op = operator.eq


class NotEqual(Criteria):
    op = operator.ne


class Contains(Criteria):
    op = operator.contains

    def build_lambda(self):
        # noqa pylint: disable=too-many-function-args
        return lambda x: self.op(self.value, getattr(x, self.attribute, ''))


class Intersects(Criteria):
    def build_lambda(self):
        value = set(self.value)

        def func(x):
            try:
                return value.intersection(set(getattr(x, self.attribute, []) or []))
            except KeyError:
                # this may happen if used to check a formdata field that didn't
                # exist when the formdata was created.
                return False

        return func


class Not(Criteria):
    def __init__(self, criteria, **kwargs):
        self.criteria = criteria

    def build_lambda(self):
        func = lambda x: False

        def combine_callables(x1, x2):
            return lambda x: not x2(x)

        func = combine_callables(func, self.criteria.build_lambda())
        return func

    def __repr__(self):
        return '<%s (%r)>' % (self.__class__.__name__, self.criteria)


class Or(Criteria):
    def __init__(self, criterias, **kwargs):
        self.criterias = criterias

    def build_lambda(self):
        func = lambda x: False

        def combine_callables(x1, x2):
            return lambda x: x1(x) or x2(x)

        for element in self.criterias:
            func = combine_callables(func, element.build_lambda())
        return func

    def __repr__(self):
        return '<%s (%r)>' % (self.__class__.__name__, self.criterias)


class And(Criteria):
    def __init__(self, criterias, **kwargs):
        self.criterias = criterias

    def build_lambda(self):
        func = lambda x: True

        def combine_callables(x1, x2):
            return lambda x: x1(x) and x2(x)

        for element in self.criterias:
            func = combine_callables(func, element.build_lambda())
        return func

    def __repr__(self):
        return '<%s (%r)>' % (self.__class__.__name__, self.criterias)


def parse_clause(clause):
    # creates a callable out of a clause
    #  (attribute, operator, value)

    if callable(clause):  # already a callable
        return clause

    def combine_callables(x1, x2):
        return lambda x: x1(x) and x2(x)

    func = lambda x: True
    for element in clause:
        if callable(element):
            func = combine_callables(func, element)
        else:
            func = combine_callables(func, element.build_lambda())
    return func


class NothingToUpdate(Exception):
    pass


class StorageIndexException(Exception):
    pass


class StoredObjectMixin:
    SLUG_DASH = '-'

    def is_readonly(self):
        return getattr(self, 'readonly', False)

    @classmethod
    def get_table_name(cls):
        return cls._names

    @classmethod
    def get_by_slug(cls, slug, ignore_errors=True):
        return cls.get_on_index(slug, 'slug', ignore_errors=ignore_errors)

    def get_new_slug(self, base=None):
        from .misc import simplify

        new_slug = simplify(base or self.name, space=self.SLUG_DASH, force_letter_first=True)[:250]
        base_new_slug = new_slug
        suffix_no = 0

        while True:
            obj = self.get_on_index(new_slug, 'slug', ignore_errors=True, ignore_migration=True)
            if obj is None or (self.id and str(obj.id) == str(self.id)):
                break
            suffix_no += 1
            new_slug = '%s%s%s' % (base_new_slug, self.SLUG_DASH, suffix_no)

        return new_slug

    def get_last_modification_info(self):
        if not get_publisher().snapshot_class:
            return None, None
        snapshots = get_publisher().snapshot_class.select_object_history(self)
        if not snapshots:
            return None, None
        return snapshots[0].timestamp, snapshots[0].user_id

    def get_applications(self):
        from wcs.applications import Application

        if getattr(self, '_applications', None) is None:
            Application.load_for_object(self)
        return self._applications

    applications = property(get_applications)

    def refresh_from_storage(self):
        obj = self.get(self.id)
        self.__dict__ = obj.__dict__

    def remove_self(self):
        assert not self.is_readonly()
        self.remove_object(self.id)
        self.id = None

    @classmethod
    def cached_get(cls, id, ignore_errors=False, **kwargs):
        pub = get_publisher()
        id = str(id)
        cached_object = pub._cached_objects[cls._names or cls._table_name].get(id)
        if isinstance(cached_object, KeyError):
            if ignore_errors:
                return None
            raise KeyError(id)
        if cached_object is not None:
            return cached_object
        o = cls.get(id, ignore_errors=True, **kwargs)
        pub._cached_objects[cls._names][id] = o if o is not None else KeyError()
        if o is None and not ignore_errors:
            raise KeyError(id)
        return o

    def __getstate__(self):
        odict = copy.copy(self.__dict__)
        if '_applications' in odict:
            del odict['_applications']
        return odict

    def __setstate__(self, ndict):
        self.__dict__ = ndict
        if hasattr(self, '_applications'):
            delattr(self, '_applications')


class StorableObject(StoredObjectMixin):
    _indexes = None
    _filename = None  # None, unless must be saved to a specific location
    _reset_class = True  # reset loaded object class

    def __init__(self, id=None):
        self.id = id

    @classmethod
    def get_objects_dir(cls):
        return os.path.join(get_publisher().app_dir, cls.get_table_name())

    @classmethod
    def keys(cls, clause=None):
        if not os.path.exists(cls.get_objects_dir()):
            return []
        if clause:
            return [x.id for x in cls.select(clause=clause)]
        return [fix_key(x) for x in os.listdir(cls.get_objects_dir()) if x[0] != '.']

    @classmethod
    def values(cls, ignore_errors=False, ignore_migration=True):
        values = [cls.get(x, ignore_errors=ignore_errors, ignore_migration=True) for x in cls.keys()]
        return [x for x in values if x is not None]

    @classmethod
    def items(cls):
        return [(x, cls.get(x)) for x in cls.keys()]

    @classmethod
    def count(cls, clause=None):
        if clause:
            return len(cls.select(clause))
        return len(cls.keys())

    @classmethod
    def exists(cls, clause=None):
        return bool(cls.count(clause))

    @classmethod
    def sort_results(cls, objects, order_by):
        if not order_by:
            return objects
        if isinstance(order_by, list):
            order_by = order_by[0]
        order_by = str(order_by)
        if order_by[0] == '-':
            reverse = True
            order_by = order_by[1:]
        else:
            reverse = False
        # only list can be sorted
        objects = list(objects)
        if order_by == 'id':
            key_function = lambda x: lax_int(x.id)
        elif order_by == 'name':
            # proper collation should be done but it's messy to get working
            # on all systems so we go the cheap and almost ok way.
            from .misc import simplify

            key_function = lambda x: simplify(x.name)
        elif order_by.endswith('_time'):
            typed_none = time.gmtime(-(10**10))  # 1653
            key_function = lambda x: getattr(x, order_by) or typed_none
        else:
            key_function = lambda x: getattr(x, order_by)
        objects.sort(key=key_function)
        if reverse:
            objects.reverse()
        return objects

    @classmethod
    def select(
        cls,
        clause=None,
        order_by=None,
        ignore_errors=False,
        ignore_migration=False,
        limit=None,
        offset=None,
        iterator=False,
        itersize=None,
        **kwargs,
    ):
        # iterator: only for compatibility with sql select()
        keys = cls.keys()
        objects = (
            cls.get(k, ignore_errors=ignore_errors, ignore_migration=ignore_migration, **kwargs) for k in keys
        )
        if ignore_errors:
            objects = (x for x in objects if x is not None)
        if clause:
            clause_function = parse_clause(clause)
            objects = (x for x in objects if clause_function(x))
        objects = cls.sort_results(objects, order_by)
        if limit or offset:
            objects = _take(objects, limit, offset)
        return list(objects)

    @classmethod
    def select_iterator(cls, **kwargs):
        yield from cls.select(**kwargs)

    @classmethod
    def has_key(cls, id):
        filename = os.path.join(cls.get_objects_dir(), fix_key(id))
        return os.path.exists(force_bytes(filename, 'utf-8'))

    @classmethod
    def get_new_id(cls, create=False):
        objects_dir = cls.get_objects_dir()
        try:
            with open(os.path.join(objects_dir, '.max_id')) as fd:
                max_id = int(fd.read())
        except (OSError, ValueError):
            max_id = 0
        keys = cls.keys()
        if not keys:
            id = max_id + 1
        else:
            id = max([lax_int(x) for x in keys] + [max_id]) + 1
            if id == 0:
                id = len(keys) + 1
        if create:
            object_filename = os.path.join(objects_dir, fix_key(id))
            try:
                fd = os.open(object_filename, os.O_CREAT | os.O_EXCL)
            except OSError:
                return cls.get_new_id(create=True)
            os.close(fd)
            with open(os.path.join(objects_dir, '.max_id'), 'w') as fd:
                fd.write(str(id))
        return str(id)

    @classmethod
    def get(cls, id, ignore_errors=False, ignore_migration=False, **kwargs):
        if id is None:
            if ignore_errors:
                return None
            raise KeyError()
        filename = os.path.join(cls.get_objects_dir(), fix_key(id))
        return cls.get_filename(
            filename, ignore_errors=ignore_errors, ignore_migration=ignore_migration, **kwargs
        )

    def get_storage_mtime(self):
        if not self.id:
            return 0
        object_filename = os.path.join(self.get_objects_dir(), fix_key(self.id))
        return os.stat(object_filename).st_mtime

    @classmethod
    def get_ids(cls, ids, ignore_errors=False, order_by=None, **kwargs):
        objects = []
        for x in ids:
            obj = cls.get(x, ignore_errors=ignore_errors, **kwargs)
            if obj is not None:
                objects.append(obj)
        return cls.sort_results(objects, order_by)

    @classmethod
    def storage_load(cls, fd):
        if get_publisher() and get_publisher().unpickler_class:
            unpickler = get_publisher().unpickler_class
        else:
            unpickler = pickle.Unpickler
        return unpickler(fd).load()

    @classmethod
    def get_filename(cls, filename, ignore_errors=False, ignore_migration=False, **kwargs):
        fd = None
        try:
            fd = open(force_bytes(filename, 'utf-8'), 'rb')  # pylint: disable=consider-using-with
            o = cls.storage_load(fd, **kwargs)
        except (OSError, ImportError, UnicodeDecodeError):
            if ignore_errors:
                return None
            raise KeyError()
        except EOFError:
            # maybe it's being written to, loop for a while to see
            current_position = fd.tell()
            for dummy in range(10):
                time.sleep(0.01)
                if current_position != os.stat(filename).st_size:
                    return cls.get_filename(
                        filename, ignore_errors=ignore_errors, ignore_migration=ignore_migration
                    )
            if ignore_errors:
                return None
            raise KeyError()
        finally:
            if fd:
                fd.close()
        if cls._reset_class:
            o.__class__ = cls
        assert not any(isinstance(k, bytes) for k in o.__dict__)
        if not ignore_migration:
            o.id = str(o.id)  # makes sure 'id' is a string
            if hasattr(cls, 'migrate'):
                o.migrate()
        return o

    def get_object_filename(self):
        if self._filename:
            if self._filename[0] == '/':
                return self._filename
            return os.path.join(get_publisher().app_dir, self._filename)
        objects_dir = self.get_objects_dir()
        return os.path.join(objects_dir, fix_key(self.id))

    @classmethod
    def storage_dumps(cls, object):
        return pickle.dumps(object, protocol=2)

    def store(self, **kwargs):
        assert not self.is_readonly()
        objects_dir = self.get_objects_dir()
        if self._filename:
            if self._filename[0] == '/':
                object_filename = self._filename
            else:
                object_filename = os.path.join(get_publisher().app_dir, self._filename)
        else:
            if not os.path.exists(objects_dir):
                try:
                    os.mkdir(objects_dir)
                except OSError as error:
                    if error.errno != 17:  # 17 == Directory exists
                        raise
            if self.id is None:
                self.id = self.get_new_id(create=True)
            object_filename = os.path.join(objects_dir, fix_key(self.id))

        s = self.storage_dumps(self)
        atomic_write(object_filename, s)
        # update last modified time
        if os.path.exists(objects_dir):
            os.utime(objects_dir, None)

    @classmethod
    def volatile(cls):
        o = cls()
        o.id = None
        return o

    @classmethod
    def remove_object(cls, id):
        objects_dir = cls.get_objects_dir()
        os.unlink(os.path.join(objects_dir, fix_key(id)))

    @classonlymethod
    def wipe(cls):
        tmpdir = tempfile.mkdtemp(prefix='wiping', dir=os.path.join(get_publisher().app_dir))
        dirs_to_move = []
        objects_dir = cls.get_objects_dir()
        dirs_to_move.append(objects_dir)

        for directory in dirs_to_move:
            if os.path.exists(directory):
                os.rename(directory, os.path.join(tmpdir, os.path.basename(directory)))

        shutil.rmtree(tmpdir)

    def __repr__(self):
        if hasattr(self, 'display_name'):
            display_name = '%r ' % self.display_name
        elif hasattr(self, 'get_display_name'):
            display_name = '%r ' % self.get_display_name()
        elif hasattr(self, 'name'):
            display_name = '%r ' % self.name
        else:
            display_name = ''
        return '<%s %sid:%s>' % (self.__class__.__name__, display_name, self.id)
