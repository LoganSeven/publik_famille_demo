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

import io
import os
import re

import django.template
from django.template import TemplateSyntaxError as DjangoTemplateSyntaxError
from django.template import VariableDoesNotExist as DjangoVariableDoesNotExist
from django.template import engines
from django.template.loader import render_to_string
from django.urls import NoReverseMatch
from django.utils.encoding import force_str, smart_str
from django.utils.safestring import mark_safe
from quixote import get_publisher, get_request, get_response, get_session
from quixote.html import TemplateIO, htmlescape, htmltext

from . import _, ezt, force_str


def get_theme_directory(theme_id):
    system_location = os.path.join(get_publisher().data_dir, 'themes', theme_id)
    local_location = os.path.join(get_publisher().app_dir, 'themes', theme_id)
    if os.path.exists(local_location):
        location = local_location
    elif os.path.exists(system_location):
        location = system_location
    else:
        return None

    while os.path.islink(location):
        location = os.path.join(os.path.dirname(location), os.readlink(location))

    if not os.path.exists(location):
        return None

    return location


def error_page(error_message, error_title=None, location_hint=None, backoffice_template_name=None):
    from . import _

    if not error_title:
        error_title = _('Error')

    get_response().set_title(error_title)

    if get_request().is_in_backoffice() and get_request().user and get_request().user.can_go_in_backoffice():
        get_response().add_javascript(['jquery.js', 'qommon.js', 'gadjo.js'])

        context = get_decorate_vars('', get_response())
        if isinstance(error_message, htmltext):
            error_message = mark_safe(error_message)
        context['error_message'] = error_message
        return QommonTemplateResponse(
            templates=[backoffice_template_name or 'wcs/backoffice/error.html'],
            context=context,
            is_django_native=True,
        )

    r = TemplateIO(html=True)
    r += htmltext('<div class="error-page">')
    r += htmltext('<p>%s</p>') % error_message
    continue_link = htmltext('<a href="%s">%s</a>') % (get_publisher().get_root_url(), _('the homepage'))
    r += htmltext('<p>%s</p>') % htmltext(_('Continue to %s')) % continue_link
    r += htmltext('</div>')
    return htmltext(r.getvalue())


def get_decorate_vars(body, response, generate_breadcrumb=True, **kwargs):
    from .publisher import get_cfg

    if response.content_type != 'text/html' or response.raw:
        return {'body': body}

    if get_request().get_header('x-popup') == 'true':
        if isinstance(body, QommonTemplateResponse):
            body.add_media()
            if body.is_django_native:
                body = render(body.templates, body.context)
        return {'body': str(body)}

    body = str(body)

    kwargs = {}
    for k, v in response.filter.items():
        if v:
            kwargs[k] = str(v)
    if 'lang' not in kwargs and hasattr(get_request(), 'language'):
        response.filter['lang'] = get_request().language

    if ('rel="popup"' in body or 'rel="popup"' in kwargs.get('sidebar', '')) or (
        'data-popup' in body or 'data-popup' in kwargs.get('sidebar', '')
    ):
        response.add_javascript(['popup.js', 'widget_list.js'])

    onload = kwargs.get('onload')
    org_name = get_cfg('sp', {}).get('organization_name', kwargs.get('default_org', get_publisher().APP_NAME))
    site_name = get_cfg('misc', {}).get('sitename', org_name)

    current_theme = get_cfg('branding', {}).get('theme', get_publisher().default_theme)

    if kwargs.get('title'):
        title = kwargs.get('title')
        page_title = kwargs.get('page_title') or title
        title_or_orgname = title
    else:
        page_title = site_name
        title = None
        title_or_orgname = site_name
    script = kwargs.get('script') or ''
    script += response.get_css_includes_for_header()
    script += response.get_javascript_for_header()

    try:
        user = get_request().user
    except Exception:
        user = None

    if type(user) in (int, str) and get_session():
        try:
            user = get_session().get_user_object()
        except KeyError:
            pass

    root_url = get_publisher().get_application_static_files_root_url()

    theme_url = '%sthemes/%s' % (root_url, current_theme)

    is_in_backoffice = response.filter.get('backoffice_page')

    if is_in_backoffice:
        header_menu = (
            get_publisher()
            .get_backoffice_root()
            .generate_header_menu(selected=kwargs.get('backoffice_section'))
        )
        user_info = kwargs.get('user_info')
        page_title = kwargs.get('title', '')
        subtitle = kwargs.get('subtitle')
        if 'sidebar' in kwargs:
            sidebar = kwargs.get('sidebar')
        css = root_url + get_publisher().qommon_static_dir + get_publisher().qommon_admin_css
        extra_head = get_publisher().get_site_option('backoffice_extra_head')
        app_label = get_publisher().get_site_option('app_label') or 'Publik'
    else:
        css = root_url + 'themes/%s/%s.css' % (current_theme, get_publisher().APP_NAME)

        # this variable is kept in locals() as it was once part of the default
        # template and existing installations may have template changes that
        # still have it.
        prelude = ''

    if generate_breadcrumb:
        breadcrumb = ''
        if hasattr(response, 'breadcrumb') and response.breadcrumb:
            s = []
            path = root_url
            if is_in_backoffice:
                path += response.breadcrumb[0][0]
                response.breadcrumb = response.breadcrumb[1:]
            total_len = sum(len(str(x[1])) for x in response.breadcrumb if x[1] is not None)
            for component, label in response.breadcrumb:
                if component.startswith(('http:', 'https:')):
                    s.append('<a href="%s">%s</a>' % (component, label))
                    continue
                if label is not None:
                    if isinstance(label, str):
                        label = htmlescape(label)
                    if not is_in_backoffice and (
                        total_len > 80 and len(label) > 10 and response.breadcrumb[-1] != (component, label)
                    ):
                        s.append('<a href="%s%s" title="%s">%s</a>' % (path, component, label, '...'))
                    else:
                        s.append('<a href="%s%s">%s</a>' % (path, component, label))
                path += component.split('#')[0]  # remove anchor for next parts
            breadcrumb = ' <span class="separator">&gt;</span> '.join(s)

    vars = response.filter.copy()
    vars.update(get_publisher().substitutions.get_context_variables())
    vars.update(locals())
    del vars['vars']  # do not create recursive dict
    return vars


def render(template_name, context):
    request = getattr(get_request(), 'django_request', None)
    result = render_to_string(template_name, context, request=request)
    return htmltext(force_str(result))


class QommonTemplateResponse:
    is_django_native = False

    def __init__(self, templates, context, is_django_native=False):
        self.templates = templates
        self.context = context
        self.is_django_native = is_django_native

    def add_media(self):
        # run add_media so we get them in the page <head>
        if 'html_form' in self.context:
            self.context['html_form'].add_media()
        if 'form' in self.context and hasattr(self.context['form'], 'add_media'):
            # legacy name, conflicting with formdata "form*" variables
            self.context['form'].add_media()


class TemplateError(Exception):
    def __init__(self, msg, params=()):
        self.msg = msg
        self.params = params

    def __str__(self):
        from . import misc

        return misc.site_encode(smart_str(self.msg) % self.params)


def ezt_raises(exception, on_parse=False):
    from . import _

    parts = []
    parts.append(
        {
            ezt.ArgCountSyntaxError: _('wrong number of arguments'),
            ezt.UnknownReference: _('unknown reference'),
            ezt.NeedSequenceError: _('sequence required'),
            ezt.UnclosedBlocksError: _('unclosed block'),
            ezt.UnmatchedEndError: _('unmatched [end]'),
            ezt.UnmatchedElseError: _('unmatched [else]'),
            ezt.BaseUnavailableError: _('unavailable base location'),
            ezt.BadFormatConstantError: _('bad format constant'),
            ezt.UnknownFormatConstantError: _('unknown format constant'),
        }.get(exception.__class__, _('unknown error'))
    )
    if exception.line is not None:
        parts.append(
            _('at line %(line)d and column %(column)d')
            % {'line': exception.line + 1, 'column': exception.column + 1}
        )
    if on_parse:
        message = _('syntax error in ezt template: %s')
    else:
        message = _('failure to render ezt template: %s')
    raise TemplateError(message % ' '.join([str(x) for x in parts]))


class Template:
    def __init__(
        self,
        value,
        raises=False,
        ezt_format=ezt.FORMAT_RAW,
        ezt_only=False,
        autoescape=True,
        record_errors=True,
    ):
        '''Guess kind of template (Django or ezt), and parse it'''
        self.value = value
        self.raises = raises
        self.record_errors = record_errors

        disable_ezt = get_publisher().has_site_option('disable-ezt-support')

        if ('{{' in value or '{%' in value) and not ezt_only:  # Django template
            self.format = 'django'
            self.render = self.django_render
            if autoescape is False:
                value = '{%% autoescape off %%}%s{%% endautoescape %%}' % value
            try:
                self.template = engines['django'].from_string(value)
            except DjangoTemplateSyntaxError as e:
                if raises:
                    raise TemplateError(_('syntax error in Django template: %s'), e)
                self.render = self.null_render

        elif '[' in value and '<!--[if gte' not in value and (ezt_only or not disable_ezt):
            # ezt template with protection against office copy/paste.
            self.format = 'ezt'
            self.render = self.ezt_render
            self.template = ezt.Template(compress_whitespace=False)
            try:
                self.template.parse(value, base_format=ezt_format)
            except ezt.EZTException as e:
                if raises:
                    ezt_raises(e, on_parse=True)
                self.render = self.null_render

        else:
            self.format = 'plain'
            self.render = self.null_render

    def null_render(self, context=None):
        return str(self.value)

    def django_render(self, context=None):
        context = context or {}
        try:
            rendered = self.template.render(context)
        except (DjangoTemplateSyntaxError, DjangoVariableDoesNotExist, NoReverseMatch) as e:
            if self.raises:
                if isinstance(e, NoReverseMatch):
                    raise TemplateError(_('invalid usage of {%% url %%} in template'))

                if isinstance(e, DjangoVariableDoesNotExist):
                    raise TemplateError(_('missing variable "%s" in template') % e.params[0])

                raise TemplateError(_('failure to render Django template: %s'), e)
            return self.value
        except Exception as e:
            if get_request() and getattr(get_request(), 'inspect_mode', False):
                raise
            if self.record_errors:
                with get_publisher().error_context(template=self.value):
                    get_publisher().record_error(exception=e, notify=True)
                if self.raises:
                    raise TemplateError(_('failure to render Django template: %s'), e)
            if self.raises:
                raise TemplateError('%s', e)  # noqa pylint: disable=raising-format-tuple
            return self.value
        rendered = str(rendered).strip()
        if context.get('allow_complex'):
            return rendered
        return re.sub(r'[\uE000-\uF8FF]', '', rendered)

    def ezt_render(self, context=None):
        context = context or {}
        fd = io.StringIO()
        try:
            self.template.generate(fd, context)
        except ezt.EZTException as e:
            if self.raises:
                ezt_raises(e)
            else:
                return self.value
        return force_str(fd.getvalue())

    @classmethod
    def is_template_string(cls, string, ezt_support=True):
        return isinstance(string, str) and (
            '{{' in string or '{%' in string or ('[' in string and ezt_support)
        )


# monkey patch django template Variable resolution to convert legacy
# strings to unicode
variable_resolve_orig = django.template.base.Variable.resolve


def variable_resolve(self, context):
    try:
        value = variable_resolve_orig(self, context)
    except UnicodeEncodeError:
        # don't crash on non-ascii variable names
        return context.template.engine.string_if_invalid
    if isinstance(value, str):
        return force_str(value, 'utf-8')
    return value


if not getattr(django.template.base.Variable, 'monkey_patched', False):
    django.template.base.Variable.resolve = variable_resolve
    django.template.base.Variable.monkey_patched = True
