# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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
import itertools
import os
import re

import django.template.loaders.filesystem
from quixote import get_publisher, get_request

from .qommon import _
from .qommon.publisher import get_cfg
from .qommon.template import get_theme_directory


class TemplateLoader(django.template.loaders.filesystem.Loader):
    def get_dirs(self):
        template_dirs = []
        if get_publisher():
            # theme set by hobo
            theme = get_publisher().get_site_option('theme', 'variables')

            # templates from tenant directory
            if theme:
                template_dirs.append(os.path.join(get_publisher().app_dir, 'templates', 'variants', theme))
                template_dirs.append(
                    os.path.join(get_publisher().app_dir, 'theme', 'templates', 'variants', theme)
                )
            template_dirs.append(os.path.join(get_publisher().app_dir, 'templates'))
            template_dirs.append(os.path.join(get_publisher().app_dir, 'theme', 'templates'))

            current_theme = get_cfg('branding', {}).get('theme', get_publisher().default_theme)
            theme_directory = get_theme_directory(current_theme)
            if theme_directory:
                # templates from theme directory
                theme_directory = os.path.join(theme_directory, 'templates')

                if theme:
                    # template theme set by hobo
                    template_dirs.append(os.path.join(theme_directory, 'variants', theme))

                template_dirs.append(theme_directory)

        return template_dirs


def grep_strings(string, hit_function):
    from wcs.data_sources import NamedDataSource
    from wcs.formdef_base import get_formdefs_of_all_kinds
    from wcs.mail_templates import MailTemplate
    from wcs.workflows import Workflow
    from wcs.wscalls import NamedWsCall

    if isinstance(string, re.Pattern):
        regex = string
    else:
        regex = re.compile('.+'.join([re.escape(x) for x in string.split()]), flags=re.I)

    def check_string(value, source_url, source_name):
        if isinstance(value, str) and regex.search(value):
            hit_function(source_url, value, source_name=source_name)
            return True

    for formdef in get_formdefs_of_all_kinds(order_by='id'):
        url = formdef.get_admin_url()
        for attr in formdef.TEXT_ATTRIBUTES:
            if check_string(getattr(formdef, attr, None), source_url=url, source_name=formdef.name):
                break

        for value in (getattr(formdef, 'workflow_options', None) or {}).values():
            if check_string(value, source_url=url + 'workflow-variables', source_name=formdef.name):
                break

        for field in formdef.fields or []:
            field._formdef = formdef
            url = formdef.get_field_admin_url(field)
            source_name = field.get_admin_url_label()
            for attr in field.get_admin_attributes():
                if check_string(getattr(field, attr, None), source_url=url, source_name=source_name):
                    break
            prefill = getattr(field, 'prefill', None)
            if prefill:
                if check_string(prefill.get('value', ''), source_url=url, source_name=source_name):
                    continue
            data_source = getattr(field, 'data_source', None)
            if data_source and data_source.get('type') in ['json', 'jsonp']:
                if check_string(data_source.get('value', ''), source_url=url, source_name=source_name):
                    continue
            elif data_source and data_source.get('type', '').startswith('carddef:'):
                if data_source['type'].count(':') == 2 and check_string(
                    data_source.get('type').split(':')[2], source_url=url, source_name=source_name
                ):
                    continue

            if field.key == 'page':
                for condition in field.get_conditions():
                    if check_string(condition.get('value', ''), source_url=url, source_name=source_name):
                        break
                else:
                    continue
            elif getattr(field, 'condition', None):
                if check_string(
                    getattr(field, 'condition').get('value', ''), source_url=url, source_name=source_name
                ):
                    continue

        if hasattr(formdef, 'options'):
            url = formdef.get_admin_url() + 'workflow-variables'
            for option_value in (formdef.options or {}).values():
                if check_string(option_value, source_url=url, source_name=formdef.name):
                    break

        if getattr(formdef, 'post_conditions', None):
            url = formdef.get_admin_url() + 'settings'
            for post_condition in formdef.post_conditions:
                condition = (post_condition.get('condition') or {}).get('value')
                if check_string(condition, source_url=url, source_name=formdef.name):
                    break

    select_kwargs = {'ignore_errors': True, 'ignore_migration': True, 'order_by': 'id'}
    for workflow in Workflow.select(**select_kwargs):
        url = workflow.get_admin_url()
        for attr in ('name', 'slug'):
            if check_string(getattr(workflow, attr, None), source_url=url, source_name=workflow.name):
                break
        for status in workflow.possible_status or []:
            url = status.get_admin_url()
            if check_string(status.name, source_url=url, source_name=workflow.name):
                continue
            check_string(status.loop_items_template, source_url=url, source_name=workflow.name)
        for global_action in workflow.global_actions or []:
            url = global_action.get_admin_url()
            check_string(global_action.name, source_url=url, source_name=workflow.name)
        for action in workflow.get_all_items():
            url = action.get_admin_url()
            for parameter in action.get_parameters():
                if check_string(getattr(action, parameter, None), source_url=url, source_name=workflow.name):
                    break
            for computed_string in action.get_computed_strings():
                if check_string(computed_string, source_url=url, source_name=workflow.name):
                    break
            for static_string in action.get_static_strings():
                if check_string(static_string, source_url=url, source_name=workflow.name):
                    break
            condition = getattr(action, 'condition', None)
            if condition:
                if check_string(condition.get('value', ''), source_url=url, source_name=workflow.name):
                    continue

        for trigger in workflow.get_all_global_action_triggers():
            url = trigger.get_admin_url()
            for computed_string in trigger.get_computed_strings():
                if check_string(computed_string, source_url=url, source_name=workflow.name):
                    break

    for obj in itertools.chain(
        NamedDataSource.select(**select_kwargs),
        NamedWsCall.select(**select_kwargs),
        MailTemplate.select(**select_kwargs),
    ):
        url = obj.get_admin_url()
        source_name = obj.name
        for attr, attr_type in obj.XML_NODES:
            attr_value = getattr(obj, attr, None)
            if attr_type == 'str':
                if check_string(attr_value, source_url=url, source_name=source_name):
                    break
            elif attr_type == 'str_list':
                for str_item in attr_value or []:
                    if check_string(str_item, source_url=url, source_name=source_name):
                        break
                else:
                    continue
                break
            elif attr_type == 'request' and attr_value:
                for str_item in itertools.chain(
                    [attr_value.get('url', '')],
                    (attr_value.get('qs_data') or {}).keys(),
                    (attr_value.get('qs_data') or {}).values(),
                    (attr_value.get('post_data') or {}).keys(),
                    (attr_value.get('post_data') or {}).values(),
                ):
                    if check_string(str_item, source_url=url, source_name=source_name):
                        break
            elif isinstance(attr_value, dict):
                for str_item in itertools.chain(attr_value.items(), attr_value.values()):
                    if check_string(str_item, source_url=url, source_name=source_name):
                        break
                else:
                    continue
                break


class record_timings:
    def __init__(self, name=None, record_if_over=None):
        self.record_if_over = record_if_over
        self.name = name

    def __call__(self, func):
        name = self.name or func.__name__

        def f(*args, **kwargs):
            request = get_request()
            timing = request.start_timing(name=name)
            try:
                return func(*args, **kwargs)
            finally:
                duration = request.stop_timing(timing)
                if self.record_if_over and duration > self.record_if_over:
                    # timings will be displayed in the traceback part of the error.
                    timings = request.timings
                    get_publisher().record_error(
                        _('%s is taking too long') % name, extra_context={'timings': timings}
                    )

        return f


def add_timing_mark(name, relative_start=None, **context):
    request = get_request()
    if not request:
        return
    return request.add_timing_mark(name, relative_start=relative_start, **context)


@contextlib.contextmanager
def add_timing_group(name, **context):
    request = get_request()
    if not request:
        yield
    else:
        with request.add_timing_group(name, **context) as record:
            yield record
