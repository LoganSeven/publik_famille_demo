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

import logging

from django.apps import apps
from django.conf import settings

from authentic2 import decorators
from authentic2.utils.cache import GlobalCache


@GlobalCache
def get_hooks(hook_name):
    """Return a list of defined hook named a2_hook<hook_name> on AppConfig classes of installed
    Django applications.

    Ordering of hooks can be defined using an orer field on the hook method.
    """
    hooks = []
    for app in apps.get_app_configs():
        name = 'a2_hook_' + hook_name
        if hasattr(app, name):
            hooks.append(getattr(app, name))
    if hasattr(settings, 'A2_HOOKS') and hasattr(settings.A2_HOOKS, 'items'):
        v = settings.A2_HOOKS.get(hook_name)
        if callable(v):
            hooks.append(v)
        v = settings.A2_HOOKS.get('__all__')
        if callable(v):
            hooks.append(lambda *args, **kwargs: v(hook_name, *args, **kwargs))
    hooks.sort(key=lambda hook: getattr(hook, 'order', 0))
    return hooks


@decorators.to_list
def call_hooks(hook_name, *args, **kwargs):
    '''Call each a2_hook_<hook_name> and return the list of results.'''
    logger = logging.getLogger(__name__)
    hooks = get_hooks(hook_name)
    for hook in hooks:
        try:
            yield hook(*args, **kwargs)
        except Exception:
            if getattr(settings, 'A2_HOOKS_PROPAGATE_EXCEPTIONS', False):
                raise
            logger.exception('exception while calling hook %s', hook)


def call_hooks_first_result(hook_name, *args, **kwargs):
    '''Call each a2_hook_<hook_name> and return the first not None result.'''
    logger = logging.getLogger(__name__)
    hooks = get_hooks(hook_name)
    for hook in hooks:
        try:
            result = hook(*args, **kwargs)
            if result is not None:
                return result
        except Exception:
            if getattr(settings, 'A2_HOOKS_PROPAGATE_EXCEPTIONS', False):
                raise
            logger.exception('exception while calling hook %s', hook)
