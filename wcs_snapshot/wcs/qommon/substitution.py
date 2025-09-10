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

import re
from contextlib import contextmanager

from quixote import get_publisher
from quixote.html import TemplateIO, htmltext


def invalidate_substitution_cache(func):
    def f(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            get_publisher().substitutions.invalidate_cache()

    return f


class Substitutions:
    substitutions_dict = {}
    dynamic_sources = []
    sources = None

    _forced_mode = None

    def __init__(self):
        self.set_empty()

    def set_empty(self):
        from wcs.data_sources import NamedDataSource
        from wcs.scripts import Script
        from wcs.variables import CardsSource, FormsSource
        from wcs.wscalls import NamedWsCall

        self.sources = [NamedDataSource, NamedWsCall, CardsSource, FormsSource, Script]

    @classmethod
    def register(cls, varname, category=None, comment=None):
        if varname in cls.substitutions_dict:
            return

        cls.substitutions_dict[varname] = {'category': category, 'comment': comment}

    @classmethod
    def register_dynamic_source(cls, klass):
        if not cls.dynamic_sources:
            cls.dynamic_sources = []
        cls.dynamic_sources.append(klass)

    @invalidate_substitution_cache
    def reset(self):
        self.set_empty()

    def feed(self, source):
        if source is None:
            # silently ignore a None source, this is for example useful when
            # adding the current user, as it may be None if he is not logged
            # in.
            return
        if source not in self.sources:
            self.sources.append(source)
            self.invalidate_cache()

    def unfeed(self, predicate):
        self.sources = [x for x in self.sources if not predicate(x)]
        self.invalidate_cache()

    @contextmanager
    def freeze(self):
        orig_sources, self.sources = self.sources, self.sources[:]
        self.invalidate_cache()
        yield
        self.sources = orig_sources
        self.invalidate_cache()

    @contextmanager
    def temporary_feed(self, source, force_mode=None):
        if source is None or source in self.sources:
            yield
            return

        orig_sources, self.sources = self.sources, self.sources[:]
        self.sources.append(source)
        self.invalidate_cache()
        old_mode, self._forced_mode = self._forced_mode, force_mode
        yield
        self._forced_mode = old_mode
        self.sources = orig_sources
        self.invalidate_cache()

    def invalidate_cache(self):
        for value in (True, False):
            if hasattr(self, '_cache_context_variables%r' % value):
                delattr(self, '_cache_context_variables%r' % value)

    def get_context_variables(self, mode=None):
        if self._forced_mode:
            mode = self._forced_mode
        lazy = get_publisher().has_site_option('force-lazy-mode')
        if not lazy and mode:
            lazy = mode in get_publisher().get_lazy_variables_modes()
        if mode == 'static':
            lazy = False
        d = getattr(self, '_cache_context_variables%r' % lazy, None)
        if d is not None:
            return d
        d = CompatibilityNamesDict()
        for source in self.sources:
            if isinstance(source, dict):
                d.update(source)
                continue
            d.update(source.get_substitution_variables())
            if not lazy and hasattr(source, 'get_static_substitution_variables'):
                d.update(source.get_static_substitution_variables())
        setattr(self, '_cache_context_variables%r' % lazy, d)
        return d

    @classmethod
    def get_substitution_html_table(cls, intro=None):
        from . import _

        r = TemplateIO(html=True)
        r += htmltext('<div class="section">')
        r += htmltext('<h3>%s</h3>') % _('Variables')
        r += htmltext('<div>')
        if intro:
            r += htmltext('<p>%s</p>') % intro
        r += htmltext('<table id="substvars" class="main">')
        r += htmltext(
            '<thead><tr><th>%s</th><th>%s</th><th>%s</th></tr></thead>'
            % (_('Category'), _('Variable'), _('Comment'))
        )
        r += htmltext('<tbody>')
        vars = [(y.get('category'), x, y.get('comment')) for x, y in cls.substitutions_dict.items()]
        for dynamic_source in cls.dynamic_sources:
            vars.extend(dynamic_source.get_substitution_variables_list())
        vars.sort()
        for category, variable, comment in vars:
            r += htmltext(
                '<tr><td>%s</td><td>%s</td><td>%s</td>' % (category, '{{ %s }}' % variable, comment)
            )
        r += htmltext('</tbody>')
        r += htmltext('</table>')
        r += htmltext('</div>')
        r += htmltext('</div>')
        return r.getvalue()


class SubtreeVar:
    def __init__(self, varname):
        self.varname = varname


class CompatibilityNamesDict(dict):
    # custom dictionary that provides automatic fallback to legacy variable
    # names (namespaced with underscores)

    valid_key_regex = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._publisher = get_publisher()

    def get(self, key, default=None):
        try:
            # noqa pylint: disable=unnecessary-dunder-call
            return self.__getitem__(key)
        except KeyError:
            return default

    def get_flat_keys(self):
        flat_keys = {}

        def flatten(base, depth=10):
            if not depth:
                return
            item = self[base]
            flat_keys[base] = item
            if hasattr(item, 'inspect_keys'):
                sub_keys = list(item.inspect_keys())
                assert len(sub_keys) == len(set(sub_keys))
            elif isinstance(item, dict):
                sub_keys = [x for x in item.keys() if self.valid_key_regex.match(x)]
            else:
                return
            for sub_key in sub_keys:
                new_depth = depth - 1
                if not isinstance(sub_key, str):
                    sub_key, recurse = sub_key
                    if not recurse:
                        new_depth = 0
                new_base = '%s_%s' % (base, sub_key)
                flat_keys[new_base] = None
                flatten(new_base, depth=new_depth)

        for key in self.keys():
            flatten(key)

        return flat_keys.keys()

    def get_path(self, base, path):
        def resolve(path):
            key = '_'.join(path)
            try:
                if base is self:
                    return dict.__getitem__(base, key)
                if hasattr(base, '__getitem__'):
                    return base[key]
                return getattr(base, key)
            except (AttributeError, KeyError, TypeError) as e:
                # TypeError will happen if indexing is used on a string
                raise KeyError(key) from e

        # longer item's names have precedence over short ones, i.e. if
        # d = {'foo': {'bar': 1}, 'foo_bar': 2}
        # then get_path(d, 'foor_bar') will return 2 and never 1.
        for i in range(len(path), 0, -1):
            try:
                value = resolve(path[:i])
                rest = path[i:]
                if rest:
                    return self.get_path(value, rest)
                return value
            except KeyError:
                pass
        raise KeyError

    def __getitem__(self, key):
        if not self.valid_key_regex.match(key):
            raise KeyError(key)
        parts = key.split('_')
        if (
            parts[-1] == 'live'
            and getattr(self._publisher, 'inspect_recurse_skip_prefixes', None) is not None
        ):
            if key not in self._publisher.inspect_recurse_skip_prefixes:
                return SubtreeVar(key)
        try:
            value = self.get_path(self, parts)
            if (
                getattr(value, 'inspect_collapse', False)
                and getattr(self._publisher, 'inspect_recurse_skip_prefixes', None) is not None
            ):
                if key not in self._publisher.inspect_recurse_skip_prefixes:
                    return SubtreeVar(key)
            return value
        except KeyError as e:
            raise KeyError(key) from e

    def __contains__(self, key):
        try:
            self.__getitem__(key)
        except KeyError:
            return False
        return True
