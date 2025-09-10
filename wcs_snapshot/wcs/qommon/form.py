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

import base64
import collections
import copy
import datetime
import decimal
import fnmatch
import hashlib
import html
import io
import json
import keyword
import mimetypes
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from functools import partial

import dns
import dns.exception
import dns.resolver
import emoji
from bleach import Cleaner, linkifier
from bleach.css_sanitizer import CSSSanitizer
from PIL import Image

try:
    import magic
except ImportError:
    magic = None

import quixote
import quixote.form.widget
from django.conf import settings
from django.utils.encoding import force_bytes, force_str
from django.utils.formats import number_format as django_number_format
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from quixote import get_publisher, get_request, get_response, get_session
from quixote.form import CheckboxWidget as QuixoteCheckboxWidget
from quixote.form import FileWidget
from quixote.form import Form as QuixoteForm
from quixote.form import HiddenWidget, IntWidget, MultipleSelectWidget, PasswordWidget, SelectWidget
from quixote.form import StringWidget as QuixoteStringWidget
from quixote.form import TextWidget as QuixoteTextWidget
from quixote.form import Widget
from quixote.html import TemplateIO, htmlescape, htmltag, htmltext, stringify

from wcs.conditions import Condition, ValidationError

from . import _, force_str, misc, ngettext
from .humantime import humanduration2seconds, seconds2humanduration, timewords
from .misc import HAS_PDFTOPPM, parse_decimal, strftime
from .publisher import get_cfg
from .template import Template, TemplateError
from .template import render as render_template
from .template_utils import render_block_to_string
from .upload_storage import PicklableUpload  # noqa pylint: disable=unused-import
from .upload_storage import UploadStorageError, get_storage_object

Widget.REQUIRED_ERROR = _('required field')
get_error_orig = Widget.get_error


def is_prefilled(self):
    return self.prefilled if hasattr(self, 'prefilled') else False


def render_title(self, title):
    if title:
        title = get_publisher().translate(title)
        if self.required:
            title += htmltext('<span title="%s" class="required">*</span>') % _('This field is required.')
        attrs = {
            'class': 'field--label',
            'id': 'form_label_%s' % self.get_name_for_id(),
        }
        if not getattr(self, 'has_inside_labels', False):
            attrs['for'] = 'form_%s' % self.get_name_for_id()
        label = htmltag('label', **attrs)
        return htmltext('<div class="title">') + label + htmltext('%s</label></div>') % title
    return ''


def render_hint(self, hint):
    if not hint:
        return ''
    if not isinstance(hint, htmltext) or not hint.startswith(htmltext('<p>')):
        hint = htmltext('<p>%s</p>') % hint
    return htmltext('<div id="form_hint_%s" class="hint">%s</div>') % (self.get_name_for_id(), hint)


def render_error(self, error):
    if not error:
        return ''
    return htmltext('<div id="form_error_%s" class="error"><p>%s</p></div>') % (self.get_name_for_id(), error)


def get_template_names(widget):
    template_names = []
    widget_template_name = getattr(widget, 'template_name', None)
    for extra_css_class in (getattr(widget, 'extra_css_class', '') or '').split():
        if not extra_css_class.startswith('template-'):
            continue
        template_name = extra_css_class.split('-', 1)[1]
        # full template
        template_names.append('qommon/forms/widgets/%s.html' % template_name)
        if widget_template_name:
            # widget specific variation
            template_names.append(widget_template_name.replace('.html', '--%s.html' % template_name))
    if widget_template_name:
        template_names.append(widget_template_name)
    template_names.append('qommon/forms/widget.html')
    return template_names


def set_aria_attributes(widget, attrs):
    hint = widget.get_hint()
    if hint:
        attrs['aria-describedby'] = 'form_hint_%s' % widget.get_name_for_id()
    error = widget.get_error()
    if error:
        attrs['aria-invalid'] = 'true'
        if 'aria-describedby' not in attrs:
            attrs['aria-describedby'] = 'form_error_%s' % widget.get_name_for_id()
        else:
            attrs['aria-describedby'] += ' form_error_%s' % widget.get_name_for_id()


def render(self):
    # quixote/form/widget.py, Widget::render
    def safe(text):
        return mark_safe(str(htmlescape(text)))

    if hasattr(self, 'add_media'):
        self.add_media()
    self.class_name = self.__class__.__name__
    self.rendered_title = lambda: safe(self.render_title(self.get_title()))
    self.rendered_error = lambda: safe(self.render_error(self.get_error()))
    self.rendered_hint = lambda: safe(self.render_hint(self.get_hint()))
    if get_publisher():
        context = get_publisher().substitutions.get_context_variables(mode='lazy')
    else:
        context = {}
    context['widget'] = self
    set_aria_attributes(widget=self, attrs=self.attrs)

    template_names = get_template_names(self)
    return htmltext(render_template(template_names, context))


def render_widget_content(self):
    # widget content (without label, hint, etc.) is reused on status page;
    # render the appropriate block.
    if get_response():
        self.add_media()
    template_names = get_template_names(self)
    context = {'widget': self}
    return htmltext(force_str(render_block_to_string(template_names, 'widget-content', context)).strip())


def widget_get_name_for_id(self):
    return self.name.replace('$', '__')


class ErrorMessage:
    # map error code and readable message
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def camel_code(self):
        return ''.join(x.lower() if i == 0 else x.capitalize() for i, x in enumerate(self.code.split('_')))


def widget_set_error_code(self, error_code):
    self.set_error(getattr(self, 'get_%s_message' % error_code)())
    self.error_code = error_code


def widget_set_error(self, error):
    self.error = error
    if self.error == Widget.REQUIRED_ERROR:
        self.error_code = 'value_missing'
    else:
        self.error_code = 'bad_input'


def widget_get_error_messages(self):
    for code in self.get_error_message_codes():
        yield ErrorMessage(code, getattr(self, 'get_%s_message' % code)())


def widget_get_error_message_codes(self):
    # always add codes that may be generated by javascript validation API
    yield 'value_missing'
    yield 'bad_input'
    yield 'type_mismatch'


Widget.render = render
Widget.cleanup = None
Widget.render_error = render_error
Widget.render_hint = render_hint
Widget.render_title = render_title
Widget.is_prefilled = is_prefilled
Widget.render_widget_content = render_widget_content
Widget.get_name_for_id = widget_get_name_for_id
Widget.get_error_messages = widget_get_error_messages
Widget.get_error_message_codes = widget_get_error_message_codes
Widget.get_value_missing_message = lambda x: Widget.REQUIRED_ERROR
Widget.get_bad_input_message = lambda x: _('Invalid value')
Widget.get_type_mismatch_message = lambda x: _('Invalid value')
Widget.set_error = widget_set_error
Widget.set_error_code = widget_set_error_code


def file_render_content(self):
    # remove trailing __file for identifier
    attrs = {'id': 'form_' + self.get_name_for_id().rsplit('__', 1)[0]}
    if self.required:
        attrs['aria-required'] = 'true'
    if self.attrs:
        attrs.update(self.attrs)
    return htmltag('input', xml_end=True, type=self.HTML_TYPE, name=self.name, value=self.value, **attrs)


FileWidget.render_content = file_render_content


class SubmitWidget(quixote.form.widget.SubmitWidget):
    def __init__(self, *args, **kwargs):
        self.extra_css_class = kwargs.pop('extra_css_class', None)
        super().__init__(*args, **kwargs)

    def render_content(self):
        if self.name in ('cancel', 'previous', 'save-draft'):
            self.attrs['formnovalidate'] = 'formnovalidate'
        label = self.label or ''
        if label and 'aria-label' not in self.attrs:
            cleaned_label = emoji.replace_emoji(label, replace='').strip()
            if cleaned_label and cleaned_label != label:
                self.attrs['aria-label'] = cleaned_label
        attrs = self.attrs
        if getattr(self, 'is_hidden', False):
            # prevent submission of form when hitting the "Enter" key
            attrs = copy.copy(attrs)
            attrs['type'] = 'button'
        return (
            htmltag('button', name=self.name, value=htmlescape(label), **attrs)
            + str(label)
            + htmltext('</button>')
        )


class RadiobuttonsWidget(quixote.form.RadiobuttonsWidget):
    template_name = 'qommon/forms/widgets/radiobuttons.html'
    has_inside_labels = True
    a11y_labelledby = True
    a11y_role = 'radiogroup'

    def __init__(self, name, value=None, **kwargs):
        self.extra_css_class = kwargs.pop('extra_css_class', None)
        self.list_css_class = kwargs.pop('list_css_class', None)
        self.option_data_attributes = kwargs.pop('option_data_attributes', [])
        self.options_with_attributes = kwargs.pop('options_with_attributes', None)
        super().__init__(name, value=value, **kwargs)

    def get_options(self):
        options = self.options_with_attributes or self.options
        for option in options:
            object, description, key = option[:3]
            yield {
                'value': key,
                'label': description,
                'disabled': bool(self.options_with_attributes and option[-1].get('disabled')),
                'selected': self.is_selected(object),
                'options': option[-1] if self.options_with_attributes else None,
            }


class RadiobuttonsWithImagesWidget(RadiobuttonsWidget):
    template_name = 'qommon/forms/widgets/radiobuttons-with-images.html'

    def add_media(self):
        get_response().add_css_include('item-with-image.css')

    def get_options(self):
        for option in self.options_with_attributes:
            obj, description, key, attributes = option[:4]
            yield {
                'value': key,
                'label': description,
                'disabled': bool(self.options_with_attributes and option[-1].get('disabled')),
                'selected': self.is_selected(obj),
                'image_url': attributes.get('image_url'),
            }


def get_selection_error_text(*args):
    return _('invalid value selected')


def set_value(self, value):
    self.value = value


SelectWidget.SELECTION_ERROR = property(get_selection_error_text)
SelectWidget.set_value = set_value


def transfer_form_value(self, request):
    # transfer form value (set in constructor, or using set_value) to
    # request.form{}
    request.form[self.name] = self.value


Widget.transfer_form_value = transfer_form_value


class Form(QuixoteForm):
    TOKEN_NOTICE = _(
        'The form you have submitted is invalid.  Most '
        'likely it has been successfully submitted once '
        'already.  Please review the form data '
        'and submit the form again.'
    )
    ERROR_NOTICE = _('There were errors processing your form.  See below for details.')

    info = None
    captcha = None

    def __init__(self, *args, **kwargs):
        self.use_tabs = kwargs.pop('use_tabs', False)
        QuixoteForm.__init__(self, *args, **kwargs)
        self.attrs['novalidate'] = 'novalidate'
        self.global_error_messages = None

    def add_captcha(self, hint=None):
        if not self.captcha and not (get_session().won_captcha or get_session().user):
            self.captcha = CaptchaWidget('captcha', hint=hint)

    def add_submit(self, name, value=None, **kwargs):
        return self.add(SubmitWidget, name, value, **kwargs)

    def add(self, widget_class, name, *args, **kwargs):
        if kwargs and 'render_br' not in kwargs:
            kwargs['render_br'] = False
        advanced = False
        if kwargs and kwargs.get('advanced', False):
            advanced = True
            del kwargs['advanced']
        default_value = kwargs.pop('default_value', Ellipsis)
        if self.use_tabs:
            tab = kwargs.pop('tab', None)
        QuixoteForm.add(self, widget_class, name, *args, **kwargs)
        widget = self._names[name]
        if 'id' not in kwargs and 'id' in widget.attrs:
            # don't let quixote3 assign a default id to widgets
            del widget.attrs['id']
        widget.advanced = advanced
        if default_value is not Ellipsis:
            widget.is_not_default = getattr(widget, 'value', None) not in (None, default_value)
        else:
            widget.is_not_default = bool(getattr(widget, 'value', None))
        if self.use_tabs:
            if tab:
                widget.tab = tab
            elif advanced:
                widget.tab = ('advanced', _('Advanced'))
            else:
                widget.tab = ('general', _('General'))
        return widget

    def set_error(self, name, error):
        super().set_error(name, force_str(error))

    def remove(self, name):
        widget = self._names.get(name)
        if widget:
            del self._names[name]
            self.widgets.remove(widget)

    def get_all_widgets(self):
        l = QuixoteForm.get_all_widgets(self)
        if self.captcha:
            l.append(self.captcha)
        return l

    def add_global_errors(self, error_messages):
        self.global_error_messages = error_messages

    def _get_default_action(self):
        if get_request().get_header('x-popup') == 'true':
            # do not leave action empty for popups, as they get embedded into
            # another URI
            return get_request().get_path()
        return QuixoteForm._get_default_action(self)

    def render_button(self, button):
        r = TemplateIO(html=True)
        classnames = '%s widget %s-button %s' % (
            button.__class__.__name__,
            button.name,
            getattr(button, 'extra_css_class', None) or '',
        )
        r += htmltext('<div class="%s">') % classnames
        r += htmltext('<div class="content">')
        r += button.render_content()
        r += htmltext('</div>')
        r += htmltext('</div>')
        r += htmltext('\n')
        return r.getvalue()

    def get_initial_tab_index(self):
        if hasattr(self, 'initial_tab'):
            return self.tabs.index(self.initial_tab)
        return 0

    def _render_start(self):
        r = TemplateIO(html=True)
        if self.use_tabs:
            r += htmltext('<div class="pk-tabs form-with-tabs">')
            self.tabs = []
            for widget in self.widgets:
                if widget.tab not in self.tabs:
                    self.tabs.append(widget.tab)
            self.tabs.sort(key=lambda x: bool(x[0] == 'advanced'))  # make "advanced" last tab
            r += htmltext('<div class="pk-tabs--tab-list" role="tablist">')
            initial_tab_index = self.get_initial_tab_index()
            for i, tab in enumerate(self.tabs):
                tab_slug, tab_label = tab
                attrs = {
                    'role': 'tab',
                    'aria-selected': 'true' if i == initial_tab_index else 'false',
                    'aria-controls': 'panel-%s' % tab_slug,
                    'id': 'tab-%s' % tab_slug,
                    'tabindex': '0' if i == initial_tab_index else '-1',
                }
                if any(getattr(x, 'is_not_default', False) for x in self.widgets if x.tab == tab):
                    attrs['class'] = 'pk-tabs--button-marker'
                r += htmltag('button', **attrs) + str(tab_label) + htmltext('</button>')
            r += htmltext('</div>')
            r += htmltext('<div class="pk-tabs--container">')
        r += super()._render_start()
        return r.getvalue()

    def _render_finish(self):
        r = super()._render_finish()
        if self.use_tabs:
            r += htmltext('</div>')
            r += htmltext('</div>')
        return r

    def _render_submit_widgets(self):
        r = TemplateIO(html=True)
        if self.submit_widgets:
            r += htmltext('<div class="buttons submit">')
            for widget in self.submit_widgets:
                r += self.render_button(widget)
            r += htmltext('</div>')
        return r.getvalue()

    def _render_error_notice(self):
        errors = []
        classnames = ['errornotice']
        if self.has_errors():
            errors.append(QuixoteForm._render_error_notice(self))
        if self.global_error_messages:
            errors.extend(self.global_error_messages)
            classnames.append('global-errors')
        t = TemplateIO(html=True)
        t += htmltext('<div class="%s" role="status">' % ' '.join(classnames))
        t += self._render_error_notice_content(errors)
        t += htmltext('</div>')
        return t.getvalue()

    def _render_error_notice_content(self, errors):
        t = TemplateIO(html=True)
        for error in errors:
            if isinstance(error, dict):
                t += (
                    htmltext(
                        '<details><summary>%(summary)s</summary><p><small>%(details)s</small></p></details>'
                    )
                    % error
                )
            elif isinstance(error, htmltext):
                t += error
            else:
                t += htmltext('<p>%s</p>') % error
        return t.getvalue()

    def _render_body(self):
        r = TemplateIO(html=True)
        if self.has_errors() or self.global_error_messages:
            r += self._render_error_notice()
        r += self._render_widgets()
        if self.captcha:
            r += self.captcha.render()
        r += self._render_submit_widgets()
        return r.getvalue()

    def _render_widgets(self):
        r = TemplateIO(html=True)
        if self.use_tabs:
            initial_tab_index = self.get_initial_tab_index()
            for i, tab in enumerate(self.tabs):
                tab_slug = tab[0]
                tab_attrs = {
                    'id': 'panel-%s' % tab_slug,
                    'role': 'tabpanel',
                    'tabindex': '0' if i == initial_tab_index else '-1',
                    'data-tab-slug': tab_slug,
                    'aria-labelledby': 'tab-%s' % tab_slug,
                }
                if i != initial_tab_index:
                    tab_attrs['hidden'] = 'hidden'
                widgets = [x for x in self.widgets if x.tab == tab]
                if widgets:
                    r += htmltag('div', **tab_attrs)
                    for widget in widgets:
                        r += widget.render()
                    r += htmltext('</div>')
            return r.getvalue()
        for widget in self.widgets:
            r += widget.render()
        return r.getvalue()

    def add_media(self):
        for widget in self.get_all_widgets():
            if hasattr(widget, 'add_media'):
                widget.add_media()

    def force_value(self, name, value):
        # backport from quixote/form/form.py (introducted in v3.1)
        widget = self.get_widget(name)
        if widget is None:
            raise ValueError('unknown widget %r' % name)
        widget.clear_error()  # calls parse internally
        widget.set_value(value)


class HtmlWidget:
    error = None
    name = None

    def __init__(self, string, title=None, *args, **kwargs):
        self.attrs = {}
        self.string = string
        self.title = title
        self.tab = kwargs.pop('tab', None)

    def render(self):
        return self.render_content()

    def render_content(self):
        content = self.title or self.string or ''
        if getattr(self, 'is_hidden', False):
            content = htmltext(str(content).replace('>', ' style="display: none">', 1))
        return htmltext(content)

    def has_error(self, request=None):
        return False

    def parse(self, *args):
        pass

    def clear_error(self, request=None):
        pass

    def transfer_form_value(self, request):
        pass


class CommentWidget(Widget):
    template_name = 'qommon/forms/widgets/comment.html'

    def __init__(self, content, extra_css_class):
        super().__init__(name='')
        self.content = content
        self.extra_css_class = extra_css_class

    def has_error(self, request=None):
        return False

    def parse(self, *args, **kwargs):
        pass

    def clear_error(self, request=None):
        pass


class CompositeWidget(quixote.form.CompositeWidget):
    content_extra_attributes = {'role': 'group'}

    def add_hidden(self, name, value=None, **kwargs):
        self.add(HiddenWidget, name, value, **kwargs)

    def transfer_form_value(self, request):
        for widget in self.get_widgets():
            widget.transfer_form_value(request)

    def render_as_thead(self):
        r = TemplateIO(html=True)
        r += htmltext('<tr>\n')
        for widget in self.get_widgets():
            r += htmltext('<th scope="col">')
            r += str(widget.get_title())
            r += htmltext('</th>')
        r += htmltext('</tr>\n')
        return r.getvalue()

    def render_content_as_tr(self):
        r = TemplateIO(html=True)
        r += htmltext('<tr>\n')
        for widget in self.get_widgets():
            extra_attributes = ''
            classnames = '%s widget' % widget.__class__.__name__
            if hasattr(widget, 'extra_css_class') and widget.extra_css_class:
                classnames += ' ' + widget.extra_css_class
            if hasattr(widget, 'content_extra_attributes'):
                extra_attributes = ' '.join(['%s=%s' % x for x in widget.content_extra_attributes.items()])
            r += htmltext('<td><div class="%s"><div class="content" %s>' % (classnames, extra_attributes))
            r += widget.render_content()
            r += widget.render_error(widget.get_error())
            r += htmltext('</div></div></td>')
        r += htmltext('</tr>\n')
        return r.getvalue()


class StringWidget(QuixoteStringWidget):
    def __init__(self, name, *args, **kwargs):
        if 'readonly' in kwargs and not kwargs.get('readonly'):
            del kwargs['readonly']
        elif 'readonly' in kwargs:
            kwargs['readonly'] = 'readonly'
        self.maxlength = kwargs.pop('maxlength', None)
        if self.maxlength:
            try:
                self.maxlength = int(self.maxlength)
            except (TypeError, ValueError):
                self.maxlength = None
        self.validation_function = kwargs.pop('validation_function', None)
        super().__init__(name, *args, maxlength=self.maxlength, **kwargs)

    def _parse(self, request):
        QuixoteStringWidget._parse(self, request)
        if self.value:
            self.value = self.value.strip()
            if self.maxlength and len(self.value) > self.maxlength:
                self.set_error_code('too_long')
            elif self.validation_function:
                try:
                    self.validation_function(self.value)
                except ValueError as e:
                    self.set_error(str(e))

    def get_too_long_message(self):
        return _('Too long, value must be at most %d characters.') % self.maxlength

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        if self.maxlength:
            yield 'too_long'

    def render_content(self):
        attrs = {'id': 'form_' + self.get_name_for_id()}
        if self.required:
            attrs['required'] = 'required'
            attrs['aria-required'] = 'true'
        if getattr(self, 'prefill_attributes', None) and 'autocomplete' in self.prefill_attributes:
            attrs['autocomplete'] = self.prefill_attributes['autocomplete']
        if self.attrs:
            attrs.update(self.attrs)
        if getattr(self, 'inputmode', None):
            attrs['inputmode'] = self.inputmode
        return htmltag('input', xml_end=True, type=self.HTML_TYPE, name=self.name, value=self.value, **attrs)


class DurationWidget(StringWidget):
    def __init__(self, name, value=None, **kwargs):
        if value:
            value = seconds2humanduration(int(value))
        if 'hint' in kwargs:
            kwargs['hint'] = str(kwargs['hint']) + htmltext('<br>')
        else:
            kwargs['hint'] = ''
        kwargs['hint'] += htmltext(_('Usable units of time: %s.')) % ', '.join(timewords())
        super().__init__(name, value=value, **kwargs)

    def parse(self, request=None):
        value = super().parse(request)
        return str(humanduration2seconds(self.value)) if value else None


class TextWidget(QuixoteTextWidget):
    prefill_attributes = None

    def __init__(self, name, *args, **kwargs):
        self.validation_function = kwargs.pop('validation_function', None)
        super().__init__(name, *args, **kwargs)

    def add_media(self):
        if self.prefill_attributes and 'geolocation' in self.prefill_attributes:
            get_response().add_javascript(['qommon.geolocation.js'])

    def get_plain_text_value(self):
        return self.value

    def _parse(self, request, use_validation_function=True):
        QuixoteTextWidget._parse(self, request)
        if self.value is not None:
            try:
                maxlength = int(self.attrs.get('maxlength', 0))
            except (TypeError, ValueError):
                maxlength = 0
            if maxlength:
                if len(self.get_plain_text_value()) > maxlength:
                    self.set_error_code('too_long')
            if use_validation_function and self.validation_function:
                try:
                    self.validation_function(self.value)
                except ValueError as e:
                    self.set_error(str(e))

    def get_too_long_message(self):
        try:
            maxlength = int(self.attrs.get('maxlength', 0))
        except (TypeError, ValueError):
            maxlength = 0
        return _('too many characters (limit is %d)') % maxlength

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        yield 'too_long'

    def render_content(self):
        attrs = {'id': 'form_' + self.get_name_for_id()}
        if self.required:
            attrs['required'] = 'required'
            attrs['aria-required'] = 'true'
        if self.attrs:
            attrs.update(self.attrs)
        if not attrs.get('cols'):
            attrs['cols'] = 72
        if not attrs.get('rows'):
            attrs['rows'] = 5
        if attrs.get('readonly') and not self.value:
            attrs['rows'] = 1
        return (
            htmltag('textarea', name=self.name, **attrs)
            + htmlescape(self.value or '')
            + htmltext('</textarea>')
        )


class CheckboxWidget(QuixoteCheckboxWidget):
    """
    Widget just like CheckboxWidget but with an effective support for the
    required attribute, if required the checkbox will have to be checked.
    """

    template_name = 'qommon/forms/widgets/checkbox.html'

    def _parse(self, request):
        self.value = self.name in request.form and not request.form[self.name] in (None, False, '', 'False')
        if self.required and not self.value:
            self.set_error(self.REQUIRED_ERROR)

    def set_value(self, value):
        if value in (None, False, '', 'False'):
            self.value = False
        else:
            self.value = True

    def render_content(self, standalone=True):
        attrs = {'id': 'form_' + self.get_name_for_id()}
        if self.required:
            attrs['aria-required'] = 'true'
        inline_title = self.attrs.pop('inline_title', '')
        if self.attrs:
            attrs.update(self.attrs)
        if attrs.pop('readonly', None) and not attrs.get('disabled'):
            # hack to restore value on click
            attrs['onclick'] = 'this.checked = !this.checked;'
        checkbox = htmltag(
            'input',
            xml_end=True,
            type='checkbox',
            name=self.name,
            value='yes',
            checked=self.value and 'checked' or None,
            **attrs,
        )
        if standalone:
            data_attrs = ' '.join('%s="%s"' % x for x in attrs.items() if x[0].startswith('data-'))
            # more elaborate markup so standalone checkboxes can be applied a
            # custom style.
            label_tag = 'span' if 'readonly' in self.attrs else 'label'
            return (
                htmltext('<%s %s>%s<span>' % (label_tag, data_attrs, checkbox))
                + str(inline_title)
                + htmltext('</span></%s>' % label_tag)
            )
        return checkbox


class UploadedFile:
    def __init__(self, directory, filename, upload):
        self.directory = directory
        self.base_filename = upload.base_filename
        self.content_type = upload.content_type

        # Find a good filename
        if filename:
            self.filename = filename
        elif self.base_filename:
            self.filename = self.base_filename
        else:
            t = datetime.datetime.now().isoformat()
            fd = tempfile.mkstemp(prefix=t, suffix='.upload', dir=self.dir_path())[0]
            os.close(fd)
            self.filename = os.path.basename(filename)

        if upload.fp:
            self.set_content(upload.fp.read())

    def set_content(self, content):
        self.size = len(content)
        file_path = self.build_file_path()
        if not os.path.exists(self.dir_path()):
            os.mkdir(self.dir_path())
        with open(file_path, 'wb') as f:
            f.write(content)

    def dir_path(self):
        return os.path.join(get_publisher().app_dir, self.directory)

    def build_file_path(self):
        return os.path.join(get_publisher().app_dir, self.directory, self.filename)

    def get_file(self):
        return open(self.build_file_path(), 'rb')  # pylint: disable=consider-using-with

    def get_file_pointer(self):
        return self.get_file()

    def get_content(self):
        with self.get_file() as fd:
            return fd.read()

    def build_response(self):
        response = get_response()
        response.content_type = self.content_type
        response.set_header('content-disposition', 'attachment; filename="%s"' % self.base_filename)
        with self.get_file() as fp:
            return fp.read()


class FileWithPreviewWidget(CompositeWidget):
    """Widget that proposes a File Upload widget but that stores the file
    ondisk so it has a "readonly" mode where the filename is shown."""

    template_name = 'qommon/forms/widgets/file.html'

    extra_css_class = 'file-upload-widget'
    max_file_size = None
    file_type = None

    max_file_size_bytes = None  # will be filled automatically

    get_value_from_token = True

    def __init__(self, name, value=None, **kwargs):
        from wcs.portfolio import has_portfolio

        self.storage = kwargs.pop('storage', None)
        try:
            self.is_remote_storage = get_storage_object(self.storage).has_redirect_url(None)
        except UploadStorageError:
            # broken, consider it as remote as files are certainly not accessible.
            self.is_remote_storage = True
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.value = value
        self.readonly = kwargs.get('readonly')
        self.max_file_size = kwargs.pop('max_file_size', None)
        self.automatic_image_resize = kwargs.pop('automatic_image_resize', False)
        self.allow_portfolio_picking = has_portfolio() and kwargs.pop('allow_portfolio_picking', True)
        if self.max_file_size:
            self.max_file_size_bytes = FileSizeWidget.parse_file_size(self.max_file_size)
        self.add(StringWidget, 'token')
        self.get_widget('token').is_hidden = True
        if not self.readonly:
            attrs = {'data-url': get_publisher().get_root_url() + 'tmp-upload'}
            if self.storage:
                attrs['data-url'] += '?storage=%s' % self.storage
            self.file_type = kwargs.pop('file_type', None)
            if self.file_type:
                attrs['accept'] = ','.join([x for x in self.file_type if x])
            if self.max_file_size_bytes:
                # this could be used for client size validation of file size
                attrs['data-max-file-size'] = str(self.max_file_size_bytes)
                attrs['data-max-file-size-human'] = str(self.max_file_size)
            self.add(FileWidget, 'file', render_br=False, attrs=attrs)
        if value:
            self.set_value(value)

    def is_image(self):
        if getattr(self, 'file_type', None):
            return all(x.startswith('image/') for x in getattr(self, 'file_type', None) or [])
        return False

    def set_value(self, value):
        if self.value and hasattr(self.value, 'close'):
            self.value.close()
        if isinstance(value, (str, dict)):
            from wcs.fields.file import FileField

            try:
                value = FileField.convert_value_from_anything(value)
            except ValueError as e:
                value = None
                if getattr(self, 'field', None):
                    get_publisher().record_error(
                        _('Failed to convert value for field "%s"') % self.field.label,
                        formdef=getattr(self, 'formdef', None),
                        exception=e,
                    )

        try:
            self.value = value
            if self.value and self.get_value_from_token:
                if not hasattr(self.value, 'token') or not get_session().get_tempfile(self.value.token):
                    # it has no token, or its token is not in the session; this may be
                    # because the file value has not been created when filling a form,
                    # or because it was restored from a draft created from an expired
                    # session. Either way, create and use a new token.
                    self.value.token = (
                        get_session().add_tempfile(self.value, storage=self.storage).get('token')
                    )
                self.get_widget('token').set_value(self.value.token)
        except Exception as e:
            if getattr(self, 'field', None):
                get_publisher().record_error(
                    _('Failed to set value on field "%s"') % self.field.label,
                    formdef=getattr(self, 'formdef', None),
                    exception=e,
                )
            self.value = None

    def add_media(self):
        get_response().add_javascript(['qommon.fileupload.js'])
        if not self.readonly and get_request().user and self.allow_portfolio_picking:
            get_response().add_javascript(['../../i18n.js', 'fargo.js'])

    def tempfile(self):
        return get_session().get_tempfile(self.get('token')) or {}

    def has_tempfile_image(self):
        temp = self.tempfile()
        if not temp:
            return False
        if not temp.get('size'):  # empty or RemoteOpaque file
            return False

        filetype = (mimetypes.guess_type(temp.get('orig_filename', '')) or [''])[0]
        if not filetype:
            return False

        if misc.is_svg_filetype(filetype):
            return True

        if filetype == 'application/pdf':
            return HAS_PDFTOPPM

        if not filetype.startswith('image/'):
            return False

        image_path = get_session().get_tempfile_path(self.get('token'))
        try:
            with Image.open(image_path):
                pass
        except Exception:
            return False

        return True

    def set_value_from_token(self, request):
        self.set_value(None)
        if self.get('token'):
            token = self.get('token')
        elif self.get('file'):
            try:
                token = get_session().add_tempfile(self.get('file'), storage=self.storage)['token']
            except UploadStorageError:
                self.set_error(_('failed to store file (system error)'))
                return
            request.form[self.get_widget('token').get_name()] = token
        else:
            token = None

        session = get_session()
        if token:
            self.value = session.get_tempfile_content(token)

    def __del__(self):
        if self.value:
            self.value.close()

    def render(self):
        file_widget = self.get_widget('file')
        if file_widget:
            set_aria_attributes(widget=self, attrs=file_widget.attrs)
        return super().render()

    def _parse(self, request):
        if self.get_value_from_token:
            self.set_value_from_token(request)

        if self.value is None:
            # there's no file, the other checks are irrelevant.
            return

        if self.storage and self.storage != self.storage:
            self.set_error(_('unknown storage system (system error)'))
            return

        # Don't trust the browser supplied MIME type, update the Upload object
        # with a MIME type created with magic (or based on the extension if the
        # module is missing).
        #
        # This also helps people uploading PDF files that were downloaded from
        # sites setting a wrong MIME type (like application/force-download) for
        # various reasons.
        if isinstance(self.value.fp, io.BufferedRandom):
            # internally recreated file, trust supplied MIME type
            filetype = self.value.content_type
        elif magic and self.value.fp:
            mime = magic.Magic(mime=True)
            filetype = mime.from_file(self.value.fp.name)
            if filetype in ('application/octet-stream', 'text/plain'):
                # second-guess libmagic as we want to accept PDF files
                # with some garbage at start.
                with open(self.value.fp.name, 'rb') as fd:
                    first_bytes = fd.read(1024)
                    if b'%PDF' in first_bytes:
                        filetype = 'application/pdf'
        else:
            filetype = getattr(self.value, 'storage_attrs', {}).get('content_type')
            if not filetype:
                filetype = mimetypes.guess_type(self.value.base_filename)[0]

        if not filetype:
            filetype = 'application/octet-stream'

        self.value.content_type = filetype

        if self.file_type:
            # validate file type
            accepted_file_types = []
            for file_type in self.file_type:
                accepted_file_types.extend(file_type.split(','))

            valid_file_type = False
            for accepted_file_type in accepted_file_types:
                # fnmatch is used to handle generic mimetypes, like
                # image/*
                if fnmatch.fnmatch(self.value.content_type, accepted_file_type):
                    valid_file_type = True
                    break
            if not valid_file_type:
                self.set_error(_('invalid file type'))

        blacklisted_file_types = get_publisher().get_site_option('blacklisted-file-types')
        if blacklisted_file_types:
            blacklisted_file_types = [x.strip() for x in blacklisted_file_types.split(',')]
        else:
            blacklisted_file_types = [
                '.exe',
                '.bat',
                '.com',
                '.pif',
                '.php',
                '.js',
                '.pht',
                '.phtml',
                '.shtml',
                '.asa',
                '.asax',
                '.cer',
                '.swf',
                '.xap',
                '.ps1',
                'application/x-ms-dos-executable',
                'text/x-php',
            ]
        if (
            self.value.base_filename
            and os.path.splitext(self.value.base_filename)[-1].lower() in blacklisted_file_types
        ) or filetype in blacklisted_file_types:
            self.set_error(_('forbidden file type'))

        if self.value.content_type in ('image/heic', 'image/heif') and not get_publisher().has_site_option(
            'do-no-transform-heic-files'
        ):
            # convert HEIC files to JPEG
            try:
                with open(self.value.fp.name, 'rb') as fd:
                    # libheic will automatically switch image orientation so we need to remove
                    # EXIF profile to avoid it being applied a second time.
                    # (graphicsmagick >= 1.3.41 have heif:ignore-transformations=false to avoid
                    # that).
                    rc = subprocess.run(
                        ['gm', 'convert', '+profile', '"*"', 'HEIC:-', 'JPEG:-'],
                        input=fd.read(),
                        capture_output=True,
                        check=True,
                    )
                    from wcs.fields.file import FileField

                    self.set_value(
                        FileField.convert_value_from_anything(
                            {
                                'content': rc.stdout,
                                'filename': os.path.splitext(self.value.base_filename)[0] + '.jpeg',
                                'content_type': 'image/jpeg',
                            }
                        )
                    )
            except subprocess.CalledProcessError:
                pass

        if self.max_file_size and hasattr(self.value, 'file_size'):
            # validate file size
            if self.value.file_size > self.max_file_size_bytes:
                self.set_error(_('over file size limit (%s)') % self.max_file_size)
                return


class EmailWidget(StringWidget):
    HTML_TYPE = 'email'
    user_part_re = re.compile(
        r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*\Z"  # dot-atom
        r'|^"([\001-\010\013\014\016-\037!#-\[\]-\177]|\\[\001-\011\013\014\016-\177])*"\Z)',  # quoted-string
        re.IGNORECASE,
    )

    def __init__(self, *args, **kwargs):
        StringWidget.__init__(self, *args, **kwargs)
        if 'size' not in kwargs:
            self.attrs['size'] = '35'

    def add_media(self):
        get_response().add_javascript(['jquery.js', '../../i18n.js', 'qommon.forms.js'])
        get_response().add_javascript_code(
            '''
            const WCS_WELL_KNOWN_DOMAINS = %s;
            const WCS_VALID_KNOWN_DOMAINS = %s;
        '''
            % (
                json.dumps(get_publisher().get_email_well_known_domains()),
                json.dumps(get_publisher().get_email_valid_known_domains()),
            )
        )

    def get_type_mismatch_message(self):
        return _('You should enter a valid email address, for example name@example.com.')

    def _parse(self, request):
        StringWidget._parse(self, request)
        if self.value is not None:
            # basic tests first
            if '@' not in self.value[1:-1]:
                self.set_error_code('type_mismatch')
                return
            if self.value[0] != '"' and ' ' in self.value:
                self.set_error_code('type_mismatch')
                return
            if self.value[0] != '"' and self.value.count('@') != 1:
                self.set_error_code('type_mismatch')
                return
            user_part, domain = self.value.rsplit('@', 1)
            if not self.user_part_re.match(user_part):
                self.set_error_code('type_mismatch')
                return
            if get_cfg('emails', {}).get('check_domain_with_dns', True):
                # testing for domain existence
                if [x for x in domain.split('.') if not x]:
                    # empty parts in domain, ex: @example..net, or
                    # @.example.net
                    self.set_error(_('Invalid address domain, you should check it and try again.'))
                    return
                domain = force_str(domain, 'utf-8', errors='ignore')
                try:
                    domain = force_str(domain.encode('idna'))
                except UnicodeError:
                    self.set_error(_('Invalid address domain, you should check it and try again.'))
                    return
                if domain == 'localhost':
                    return
                try:
                    dns.resolver.resolve(force_str(domain), 'MX')
                except dns.exception.DNSException:
                    self.set_error(_('Invalid address domain, you should check it and try again.'))


class OptGroup:
    def __init__(self, title):
        self.title = title

    def __eq__(self, other):
        return isinstance(other, OptGroup) and self.title == other.title


class SingleSelectWidget(quixote.form.widget.SingleSelectWidget):
    def __init__(self, *args, **kwargs):
        self.autocomplete = bool('data-autocomplete' in kwargs)
        super().__init__(*args, **kwargs)

    def get_allowed_values(self):
        return [item[0] for item in self.options if not isinstance(item[0], OptGroup)]

    def set_options(self, options, *args, **kwargs):
        # options can be,
        # - [objects:any, ...]
        # - [(object:any, description:any), ...]
        # - [(object:any, description:any, key:any), ...]
        # - [(object:any, description:any, key:any, html_attrs:dict), ...]
        self.options = []  # for compatibility with existing quixote methods
        self.full_options = []
        for option in options:
            if isinstance(option, (tuple, list)):
                if len(option) == 2:
                    option_tuple = (option[0], option[1], stringify(option[1]))
                elif len(option) == 3:
                    option_tuple = (option[0], option[1], stringify(option[2]))
                elif len(option) >= 4:
                    option_tuple = (option[0], option[1], stringify(option[2]), option[3])
            else:
                option_tuple = (option, option, stringify(option))
            self.full_options.append(option_tuple)
        self.full_options = [x + ({},) if len(x) == 3 else x for x in self.full_options]
        self.options = [x[:3] for x in self.full_options]

    def add_media(self):
        if self.autocomplete:
            get_response().add_javascript(['select2.js'])

    def render_content(self):
        attrs = {'id': 'form_' + self.get_name_for_id()}
        if self.required:
            attrs['required'] = 'required'
            attrs['aria-required'] = 'true'
        if self.attrs:
            attrs.update(self.attrs)
        tags = [htmltag('select', name=self.name, **attrs)]
        opened_optgroup = False
        for obj, description, key, attrs in self.full_options:
            if isinstance(obj, OptGroup):
                if opened_optgroup:
                    tags.append(htmltext('</optgroup>'))
                tags.append(htmltag('optgroup', label=obj.title))
                opened_optgroup = True
                continue
            if self.is_selected(obj):
                selected = 'selected'
            else:
                selected = None
            if description is None:
                description = ''
            r = htmltag('option', value=key, selected=selected, **attrs)
            tags.append(r + htmlescape(description) + htmltext('</option>'))
        if opened_optgroup:
            tags.append(htmltext('</optgroup>'))
        tags.append(htmltext('</select>'))
        return htmltext('\n').join(tags)


class ValidationCondition(Condition):
    def __init__(self, django_condition, value):
        super().__init__({'type': 'django', 'value': django_condition})
        self.evaluated_value = value

    def get_data(self):
        data = get_publisher().get_substitution_variables()
        data['value'] = self.evaluated_value
        return data


class ValidationWidget(CompositeWidget):
    validation_methods = collections.OrderedDict(
        [
            (
                'digits',
                {
                    'title': _('Digits'),
                    'regex': r'\d+',
                    'error_message': _('You should enter digits only, for example: 123.'),
                    'html_inputmode': 'numeric',
                },
            ),
            (
                'time',
                {
                    'title': _('Time'),
                    'regex': r'([01]?[0-9]|2[0-3]):[0-5][0-9]',
                    'error_message': _('You should enter a valid time, between 00:00 and 23:59.'),
                    'html_input_type': 'time',
                },
            ),
            (
                'url',
                {
                    'title': _('URL'),
                    'function': 'validate_url',
                    'error_message': _('You should enter a valid URL, starting with http:// or https://.'),
                    'html_input_type': 'url',
                },
            ),
            (
                'phone',
                {
                    'title': _('Phone Number'),
                    'regex': r'\+?[-\(\)\d\.\s/]+',
                    'error_message': _('You should enter a valid phone number.'),
                    'html_input_type': 'tel',
                    'normalize_for_fts': misc.normalize_phone_number_for_fts,
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'phone-fr',
                {
                    'title': _('Phone Number (France)'),
                    'function': 'validate_phone_fr',
                    'error_message': _(
                        'You should enter a valid 10-digits phone number, for example 06 39 98 89 93.'
                    ),
                    'html_input_type': 'tel',
                    'normalize_for_fts': misc.normalize_phone_number_for_fts,
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'mobile-local',
                {
                    'title': _('Mobile Phone Number (local)'),
                    'function': 'validate_mobile_phone_local',
                    'error_message': _('You should enter a valid mobile phone number.'),
                    'html_input_type': 'tel',
                    'normalize_for_fts': misc.normalize_phone_number_for_fts,
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'zipcode-fr',
                {
                    'title': _('Zip Code (France)'),
                    'regex': r'\d{5}',
                    'error_message': _('You should enter a 5-digits zip code, for example 75014.'),
                    'html_inputmode': 'numeric',
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'siren-fr',
                {
                    'title': _('SIREN Code (France)'),
                    'function': 'validate_siren',
                    'error_message': _(
                        'You should enter a valid 9-digits SIREN code, for example 443170139.'
                    ),
                    'html_inputmode': 'numeric',
                    'normalize_function': lambda v: v.upper().strip().replace(' ', ''),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'siret-fr',
                {
                    'title': _('SIRET Code (France)'),
                    'function': 'validate_siret',
                    'error_message': _(
                        'You should enter a valid 14-digits SIRET code, for example 44317013900036.'
                    ),
                    'html_inputmode': 'numeric',
                    'normalize_function': lambda v: v.upper().strip().replace(' ', ''),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'nir-fr',
                {
                    'title': _('NIR (France)'),
                    'error_message': _(
                        'You should enter a valid 15-digits social security number, '
                        'for example 294037512000522.'
                    ),
                    'function': 'validate_nir',
                    'normalize_function': lambda v: v.upper().strip().replace(' ', ''),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'ants-predemand-fr',
                {
                    'title': _('ANTS predemand number'),
                    'regex': r'[a-zA-Z0-9]{10}',
                    'error_message': _(
                        'You should enter a valid ANTS predemand number, for example MLCE4EC23X.'
                    ),
                    'normalize_function': lambda v: v.upper(),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'caf-beneficiary-number-fr',
                {
                    'title': _('CAF beneficiary number'),
                    'regex': r'\d{7}',
                    'error_message': _(
                        'You should enter a valid CAF beneficiary number, for example 4325932.'
                    ),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'tax-assessment-fiscal-number-fr',
                {
                    'title': _('Tax assessment - Fiscal number'),
                    'regex': r'\d{13}',
                    'error_message': _(
                        'You should enter a valid fiscal number, for example 12 34 567 891 234.'
                    ),
                    'normalize_function': lambda v: v.replace(' ', ''),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'tax-assessment-reference-fr',
                {
                    'title': _('Tax assessment - Assessment reference'),
                    'regex': r'\d{13}',
                    'error_message': _(
                        'You should enter a valid assessment reference, for example 12 34 567 891 234.'
                    ),
                    'normalize_function': lambda v: v.replace(' ', ''),
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'rna-number-fr',
                {
                    'title': _('RNA number'),
                    'regex': r'W(\d{9}|2[AB]\d{7}|9\w\d{7})',
                    'error_message': _('You should enter a valid RNA number, for example W743525491.'),
                    'normalize_function': lambda v: v.upper().strip().replace(' ', ''),
                },
            ),
            (
                'nrn-be',
                {
                    'title': _('National register number (Belgium)'),
                    'error_message': _(
                        'You should enter a valid 11-digits national register number, '
                        'for example 85073003328.'
                    ),
                    'html_inputmode': 'numeric',
                    'function': 'validate_belgian_nrn',
                    'display_as_string_in_spreadsheets': True,
                },
            ),
            (
                'iban',
                {
                    'title': _('IBAN'),
                    'function': 'validate_iban',
                    'error_message': _(
                        'You should enter a valid IBAN code, it should have between 14 and 34 characters, '
                        'for example FR7600001000010000000000101.'
                    ),
                    'normalize_function': lambda v: v.upper().strip().replace(' ', ''),
                },
            ),
            ('regex', {'title': _('Regular Expression')}),
            ('django', {'title': _('Django Condition')}),
        ]
    )

    def __init__(self, name, value=None, **kwargs):
        super().__init__(name, value=value, **kwargs)
        if not value:
            value = {}

        disabled_validation_types = [
            x.strip()
            for x in get_publisher().get_site_option('disabled-validation-types').split(',')
            if x.strip()
        ]
        options = [(None, _('None'), '')] + [
            (x, y['title'], x)
            for x, y in self.validation_methods.items()
            if not any(fnmatch.fnmatch(x, y) for y in disabled_validation_types)
        ]

        self.add(
            SingleSelectWidget,
            'type',
            options=options,
            value=value.get('type'),
            attrs={'data-dynamic-display-parent': 'true'},
        )
        self.parse()
        if not self.value:
            self.value = {}

        self.add(
            RegexStringWidget,
            'value_regex',
            size=80,
            value=value.get('value') if value.get('type') == 'regex' else None,
            attrs={
                'data-dynamic-display-child-of': 'validation$type',
                'data-dynamic-display-value': 'regex',
            },
        )
        self.add(
            DjangoConditionWidget,
            'value_django',
            size=80,
            value=value.get('value') if value.get('type') == 'django' else None,
            attrs={
                'data-dynamic-display-child-of': 'validation$type',
                'data-dynamic-display-value': 'django',
            },
        )
        self.add(
            StringWidget,
            'error_message',
            size=80,
            value=value.get('error_message') if value.get('type') else None,
            title=_('Custom error message'),
            hint=_(
                'This message will be be displayed if validation fails. '
                'An empty value will give the default error message.'
            ),
            attrs={
                'data-dynamic-display-child-of': 'validation$type',
                'data-dynamic-display-value-in': '|'.join([x[2] for x in options if x[2]]),
            },
        )
        self._parsed = False

    def _parse(self, request):
        values = {}
        type_ = self.get('type')
        if type_:
            values['type'] = type_
            value = self.get('value_%s' % type_)
            if value:
                values['value'] = value
            error_message = self.get('error_message')
            if error_message:
                default_error_message = self.validation_methods[type_].get('error_message')
                if error_message != default_error_message:
                    values['error_message'] = error_message
        self.value = values or None

    def render_content(self):
        r = TemplateIO(html=True)
        inlines = ['type', 'value_regex', 'value_django']
        for name in inlines:
            widget = self.get_widget(name)
            r += widget.render_error(widget.get_error())
        for name in inlines:
            widget = self.get_widget(name)
            r += widget.render_content()
        widget = self.get_widget('error_message')
        r += widget.render()
        error_messages = {
            x: str(y.get('error_message'))
            for x, y in self.validation_methods.items()
            if y.get('error_message')
        }
        r += htmltext(
            '<script id="validation-error-messages" type="application/json">%s</script>'
            % json.dumps(error_messages)
        )
        return r.getvalue()

    @classmethod
    def get_validation_function(cls, validation):
        pattern = cls.get_validation_pattern(validation)
        if pattern:

            def regex_validation(value):
                return bool(re.match(r'^(?:%s)$' % pattern, value))

            return regex_validation
        if validation['type'] == 'django' and validation.get('value'):

            def django_validation(value):
                condition = ValidationCondition(validation['value'], value=value)
                return condition.evaluate()

            return django_validation
        validation_method = cls.validation_methods.get(validation['type'])
        if validation_method and 'function' in validation_method:
            return getattr(misc, validation_method['function'])

    @classmethod
    def get_validation_error_message(cls, validation):
        if validation.get('error_message'):
            return get_publisher().translate(validation.get('error_message'))
        validation_method = cls.validation_methods.get(validation['type'])
        if validation_method and 'error_message' in validation_method:
            return validation_method['error_message']

    @classmethod
    def get_validation_pattern(cls, validation):
        validation_method = cls.validation_methods.get(validation['type'])
        if validation_method and validation_method.get('regex'):
            return validation_method.get('regex')
        if validation['type'] == 'regex':
            return validation.get('value')
        return None

    @classmethod
    def get_normalize_function(cls, validation):
        validation_method = cls.validation_methods.get(validation['type'])
        if validation_method and validation_method.get('normalize_function'):
            return validation_method['normalize_function']
        return lambda x: x  # identity

    @classmethod
    def get_html_input_type(cls, validation):
        validation_method = cls.validation_methods.get(validation['type'])
        if validation_method and validation_method.get('html_input_type'):
            return validation_method.get('html_input_type')
        return 'text'

    @classmethod
    def get_html_inputmode(cls, validation):
        validation_method = cls.validation_methods.get(validation['type'])
        if validation_method and validation_method.get('html_inputmode'):
            return validation_method.get('html_inputmode')


class WcsExtraStringWidget(StringWidget):
    field = None
    prefill = False
    prefill_attributes = None
    validation_function = None
    validation_function_error_message = None

    def add_media(self):
        if self.prefill_attributes and 'geolocation' in self.prefill_attributes:
            get_response().add_javascript(['qommon.geolocation.js'])

    def render_content(self):
        if self.field and self.field.validation:
            self.HTML_TYPE = ValidationWidget.get_html_input_type(self.field.validation)
            self.inputmode = ValidationWidget.get_html_inputmode(self.field.validation)
        return super().render_content()

    def get_bad_input_message(self):
        validation_function_error_message = self.validation_function_error_message
        if self.field and self.field.validation:
            validation_function_error_message = ValidationWidget.get_validation_error_message(
                self.field.validation
            )
        return validation_function_error_message or _('invalid value')

    def _parse(self, request):
        StringWidget._parse(self, request)
        if self.field and self.field.validation and self.value is not None:
            self.validation_function = ValidationWidget.get_validation_function(self.field.validation)

        normalized_value = self.value
        if self.field and self.value and self.field.validation:
            normalize = ValidationWidget.get_normalize_function(self.field.validation)
            normalized_value = normalize(self.value)

        if self.value and self.validation_function and not self.validation_function(normalized_value):
            self.set_error_code('bad_input')

        if self.field and self.value and not self.error and self.field.validation:
            self.value = normalized_value


class NumericWidget(WcsExtraStringWidget):

    def __init__(self, name, value=None, **kwargs):
        self.restrict_to_integers = kwargs.pop('restrict_to_integers', False)
        self.inputmode = 'numeric' if self.restrict_to_integers else 'decimal'
        self.min_value = kwargs.pop('min_value', None)
        self.max_value = kwargs.pop('max_value', None)
        super().__init__(name, value=value, **kwargs)

    def _parse(self, request):
        request = request or get_request()
        value = request.form.get(self.name)
        if value == '':
            value = None
        try:
            self.value = parse_decimal(value, do_raise=True, keep_none=True)
        except (ArithmeticError, TypeError, ValueError):
            self.set_error_code('bad_input')
            self.value = None
        if self.value is not None:
            if self.min_value is not None and self.value < self.min_value:
                self.set_error_code('range_underflow')
            elif self.max_value is not None and self.value > self.max_value:
                self.set_error_code('range_overflow')
            elif self.restrict_to_integers and int(self.value) != self.value:
                self.set_error_code('type_mismatch')

    def set_value(self, value):
        try:
            super().set_value(parse_decimal(value, do_raise=True, keep_none=True))
        except (ArithmeticError, TypeError, ValueError):
            super().set_value(None)

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        yield 'range_underflow'
        yield 'range_overflow'

    def get_bad_input_message(self):
        if self.restrict_to_integers:
            return _('You should enter digits only, for example: 123.')
        return _('You should enter a number, for example: 123.')

    def get_range_underflow_message(self):
        return _('You should enter a number greater than or equal to %s.') % django_number_format(
            self.min_value
        )

    def get_range_overflow_message(self):
        return _('You should enter a number less than or equal to %s.') % django_number_format(self.max_value)

    def get_type_mismatch_message(self):
        return _('You should enter a number without a decimal separator.')

    def render_content(self):
        if isinstance(self.value, decimal.Decimal):
            self.value = django_number_format(self.value)
        return super().render_content()


class DateWidget(StringWidget):
    '''StringWidget which checks the value entered is a correct date'''

    template_name = 'qommon/forms/widgets/date.html'

    minimum_date = None
    maximum_date = None
    content_extra_css_class = 'date'

    def __init__(self, name, value=None, **kwargs):
        minimum_date = kwargs.pop('minimum_date', None)
        if minimum_date:
            self.minimum_date = misc.get_as_datetime(minimum_date)
        maximum_date = kwargs.pop('maximum_date', None)
        if maximum_date:
            self.maximum_date = misc.get_as_datetime(maximum_date)
        if kwargs.pop('minimum_is_future', False):
            if kwargs.get('date_can_be_today'):
                self.minimum_date = datetime.date.today()
            else:
                self.minimum_date = datetime.datetime.today() + datetime.timedelta(1)
        if kwargs.pop('date_in_the_past', False):
            if kwargs.get('date_can_be_today'):
                self.maximum_date = datetime.date.today()
            else:
                self.maximum_date = datetime.datetime.today() - datetime.timedelta(1)

        if 'date_can_be_today' in kwargs:
            del kwargs['date_can_be_today']

        if isinstance(value, (datetime.date, datetime.datetime)):
            value = value.strftime(misc.date_format())

        StringWidget.__init__(self, name, value=value, **kwargs)
        self.attrs['size'] = '12'
        self.attrs['maxlength'] = '10'

    def transfer_form_value(self, request):
        if isinstance(self.value, time.struct_time):
            request.form[self.name] = strftime(misc.date_format(), self.value)
        else:
            super().transfer_form_value(request)

    def parse(self, request=None):
        StringWidget.parse(self, request=request)
        return self.value

    @classmethod
    def get_format_string(cls):
        return misc.date_format()

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        yield 'range_underflow'
        yield 'range_overflow'

    def get_type_mismatch_message(self):
        return _('You should enter a valid date.')

    def get_range_underflow_message(self):
        return _('You should enter a valid date. It must be on or after %s.') % strftime(
            misc.date_format(), self.minimum_date
        )

    def get_range_overflow_message(self):
        return _('You should enter a valid date. It must be on or before %s.') % strftime(
            misc.date_format(), self.maximum_date
        )

    def _parse(self, request):
        StringWidget._parse(self, request)
        if self.value is not None:
            try:
                value = misc.get_as_datetime(self.value).timetuple()
                self.value = strftime(self.get_format_string(), value)
            except ValueError:
                self.set_error_code('type_mismatch')
                self.value = None
                return
            if value[0] < 1500 or value[0] > 2099:
                self.set_error_code('type_mismatch')
                self.value = None
            elif self.minimum_date and value[:3] < self.minimum_date.timetuple()[:3]:
                self.set_error(
                    _('You should enter a valid date. It must be on or after %s.')
                    % strftime(misc.date_format(), self.minimum_date)
                )
            elif self.maximum_date and value[:3] > self.maximum_date.timetuple()[:3]:
                self.set_error(
                    _('You should enter a valid date. It must be on or before %s.')
                    % strftime(misc.date_format(), self.maximum_date)
                )

    def add_media(self):
        pass


class TimeWidget(DateWidget):
    template_name = 'qommon/forms/widgets/time.html'

    def _parse(self, request):
        StringWidget._parse(self, request)
        if self.value is not None:
            try:
                value = datetime.datetime.strptime(self.value, self.get_format_string())
                self.value = strftime(self.get_format_string(), value)
            except ValueError:
                self.set_error(_('You should enter a valid time, between 00:00 and 23:59.'))
                self.value = None

    @classmethod
    def get_format_string(cls):
        return '%H:%M'


class DateTimeWidget(CompositeWidget):
    def __init__(self, name, value=None, use_datetime_object=False, **kwargs):
        self.use_datetime_object = use_datetime_object
        super().__init__(name, value=value, **kwargs)
        date_value = None
        time_value = None
        if value:
            if self.use_datetime_object:
                date_value = value.date()
                time_value = value.time()
            else:
                date_value = misc.get_as_datetime(value).strftime(DateWidget.get_format_string())
                time_value = misc.get_as_datetime(value).strftime(TimeWidget.get_format_string())
        self.add(DateWidget, 'date', value=date_value, render_br=False)
        self.add(TimeWidget, 'time', value=time_value, render_br=False)

    def render_content(self):
        r = TemplateIO(html=True)
        for widget in self.get_widgets():
            r += widget.render_widget_content()
        return r.getvalue()

    def _parse(self, request):
        date = self.get('date')
        time = self.get('time')
        if not date and not time:
            self.value = None
            return self.value
        time = time or '00:00'
        try:
            self.value = misc.get_as_datetime('%s %s' % (date, time))
        except ValueError:
            self.set_error(_('invalid value'))
        if not self.use_datetime_object:
            self.value = '%s %s' % (date, time)
        return self.value


class RegexStringWidget(StringWidget):
    '''StringWidget which checks the value entered is a correct regex'''

    def _parse(self, request):
        StringWidget._parse(self, request)
        if self.value is not None:
            try:
                re.compile(self.value)
            except Exception:
                self.set_error(_('invalid regular expression'))
                self.value = None


class CheckboxesWidget(Widget):
    readonly = False
    template_name = 'qommon/forms/widgets/checkboxes.html'
    a11y_role = 'group'
    a11y_labelledby = True
    has_inside_labels = True
    load_options_to_check_for_errors = True

    def __init__(self, name, value=None, options=None, **kwargs):
        self.options = options
        self.options_with_attributes = kwargs.pop('options_with_attributes', None)
        self.inline = kwargs.pop('inline', True)
        self.min_choices = int(kwargs.pop('min_choices', 0) or 0)
        self.max_choices = int(kwargs.pop('max_choices', 0) or 0)
        if 'readonly' in kwargs:
            del kwargs['readonly']
            self.readonly = True
        super().__init__(name, value, **kwargs)

    def is_selected(self, value):
        return bool(self.value and value in self.value)

    def get_options(self):
        options = self.options_with_attributes or self.options
        group_by = None
        for i, option in enumerate(options):
            if len(option) == 2:
                obj, description, key = (option[0], option[1], str(i))
            else:
                obj, description, key = option[:3]

            if self.options_with_attributes and option[-1].get('group_by'):
                if option[-1].get('group_by') != group_by:
                    if group_by:
                        yield {'end_optgroup': group_by}
                    yield {'start_optgroup': option[-1].get('group_by')}
                group_by = option[-1].get('group_by')

            yield {
                'name': self.name + '$element%s' % key,
                'value': obj,
                'label': description,
                'disabled': bool(self.options_with_attributes and option[-1].get('disabled')),
                'selected': self.is_selected(obj),
                'options': option[-1] if self.options_with_attributes else None,
            }

        if group_by:
            yield {'end_optgroup': group_by}

    def _parse(self, request):
        if self.readonly:
            return
        values = []
        if self.load_options_to_check_for_errors:
            for option in self.get_options():
                if option.get('disabled') or not option.get('name'):
                    continue
                name = option['name']
                if name in request.form and not request.form[name] in (False, '', 'False'):
                    values.append(option['value'])
        else:
            # get a list of the correct length, with no regards for actual option
            # names, this is just used for live validation.
            values = [
                x
                for x, y in request.form.items()
                if x.startswith(self.name + '$element') and y not in (False, '', 'False')
            ]
        self.value = values or None
        if self.required and not self.value:
            self.set_error(self.REQUIRED_ERROR)
        if self.value and self.min_choices and len(self.value) < self.min_choices:
            self.set_error_code('too_short')
        if self.value and self.max_choices and len(self.value) > self.max_choices:
            self.set_error_code('too_long')

    def get_too_short_message(self):
        return ngettext(
            'You must select at least %(min_choices)d answer.',
            'You must select at least %(min_choices)d answers.',
            self.min_choices,
        ) % {'min_choices': self.min_choices}

    def get_too_long_message(self):
        return ngettext(
            'You must select at most %(max_choices)d answer.',
            'You must select at most %(max_choices)d answers.',
            self.max_choices,
        ) % {'max_choices': self.max_choices}

    def set_value(self, value):
        if isinstance(value, str) and '|' in value:
            self.value = value.split('|')
        else:
            super().set_value(value)

    def transfer_form_value(self, request):
        for v in self.value or []:
            for option in self.get_options():
                if option.get('value') == v:
                    request.form[option['name']] = True

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        if self.min_choices:
            yield 'too_short'
        if self.max_choices:
            yield 'too_long'


class CheckboxesWithImagesWidget(CheckboxesWidget):
    template_name = 'qommon/forms/widgets/checkboxes-with-images.html'

    def add_media(self):
        get_response().add_css_include('item-with-image.css')


class ValidatedStringWidget(StringWidget):
    '''StringWidget which checks the value entered is correct according to a regex'''

    regex = None

    def __init__(self, *args, **kwargs):
        if 'regex' in kwargs:
            self.regex = kwargs.pop('regex')
        super().__init__(*args, **kwargs)

    def _parse(self, request):
        StringWidget._parse(self, request)
        if self.regex and self.value is not None:
            match = re.match(self.regex, self.value)
            if not match or not match.group() == self.value:
                self.set_error(_('wrong format'))


class UrlWidget(ValidatedStringWidget):
    '''StringWidget which checks the value entered is a correct url starting with http or https'''

    regex = r'^https?://.+'

    def _parse(self, request):
        ValidatedStringWidget._parse(self, request)
        if self.error:
            self.set_error(_('must start with http:// or https:// and have a domain name'))


class VarnameWidget(ValidatedStringWidget):
    """StringWidget which checks the value entered is a syntactically correct
    variable name."""

    regex = r'^[a-zA-Z][a-zA-Z0-9_]*'

    def render_content(self):
        r = TemplateIO(html=True)
        r += super().render_content()  # <input>
        r += htmltext('<span style="display: none" class="inline-hint-message">%s</span>') % _(
            'This identifier is also used by another field.'
        )
        return r.getvalue()

    def _parse(self, request):
        ValidatedStringWidget._parse(self, request)
        if self.error:
            self.set_error(_('must only consist of letters, numbers, or underscore'))
        # forbid id/text to be used as identifier, as they would clash against
        # "native" id/text keys in datasources; forbid "status" to avoid status
        # filtering being diverted to a form field.
        # And forbid all reserved Python keywords so varnames can be used in
        # dotted expressions (form.var.plop).
        if self.value in ('id', 'text', 'status') + tuple(keyword.kwlist):
            self.error = _('this value is reserved for internal use.')


class SlugWidget(ValidatedStringWidget):
    def __init__(self, name, value=None, **kwargs):
        if 'title' not in kwargs:
            kwargs['title'] = _('Identifier')
        if 'required' not in kwargs:
            kwargs['required'] = True
        if 'size' not in kwargs:
            kwargs['size'] = 50
        self.had_uppercase_value = bool(value and value != value.lower())
        super().__init__(name, value=value, **kwargs)

    @property
    def regex(self):
        if self.had_uppercase_value:
            # do not break existing values using uppercase letters
            return r'^[a-zA-Z][a-zA-Z0-9_-]*'
        return r'^[a-z][a-z0-9_-]*'

    def _parse(self, request):
        super()._parse(request)
        if self.error:
            self.set_error(
                _(
                    'wrong format: must start with a letter and '
                    'must only consist of letters, numbers, dashes, or underscores'
                )
            )


class FileSizeWidget(ValidatedStringWidget):
    """StringWidget which checks the value entered is a syntactically correct
    file size."""

    regex = r'^\s*([\d]+)\s*([MKk]i?)?[oB]?\s*$'

    def __init__(self, *args, **kwargs):
        hint = kwargs.pop('hint', _('Accepted units: MB (megabytes), kB (kilobytes), for example: 3 MB'))
        ValidatedStringWidget.__init__(self, *args, hint=hint, **kwargs)

    @classmethod
    def parse_file_size(cls, value):
        try:
            value, unit = re.search(cls.regex, value).groups()
        except AttributeError:  # None has no .groups()
            raise ValueError()
        coeffs = {
            'Mi': 2**20,
            'Ki': 2**10,
            'ki': 2**10,
            'M': 10**6,
            'K': 10**3,
            'k': 10**3,
            None: 1,
        }
        return int(value) * coeffs.get(unit)

    def _parse(self, request):
        ValidatedStringWidget._parse(self, request)
        if self.error:
            self.set_error(_('invalid file size'))


class CaptchaWidget(CompositeWidget):
    def __init__(self, name, value=None, mode='arithmetic-simple', *args, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.render_br = False
        if value:
            token = value
        else:
            token = get_session().create_captcha_token()
        hidden_input = htmltext('<input type="hidden" name="%s$token" value="%s"></input>')
        self.add(HtmlWidget, 'token', title=hidden_input % (self.name, token['token']))

        # create question, and fill token['answer']
        a, b = random.randint(2, 9), random.randint(2, 9)
        while b == a:
            # don't get twice the same number
            b = random.randint(2, 9)
        if mode == 'arithmetic-simple':
            operator = random.choice([_('plus'), _('minus')])
        else:
            operator = random.choice([_('times'), _('plus'), _('minus')])
        if operator == _('times'):
            answer = a * b
        elif operator == _('plus'):
            answer = a + b
        elif operator == _('minus'):
            if b > a:
                a, b = b, a
            answer = a - b
        self.question = _('What is the result of %(a)d %(op)s %(b)d?') % {
            'a': a,
            'b': b,
            'op': operator,
        }
        self.hint = kwargs.get('hint')
        if self.hint is None:
            self.hint = _('Please answer this simple mathematical question as proof you are not a bot.')
        self.add(StringWidget, 'q', required=True, attrs={'required': 'required'})
        token['answer'] = str(answer)

    def _parse(self, request):
        v = {'answer': self.get('q'), 'token': request.form.get('%s$token' % self.name)}
        token = get_session().get_captcha_token(v['token'])
        if v['answer'] and token and token['answer'] == v['answer'].strip():
            get_session().won_captcha = True
            self.value = v
        elif v['answer']:
            self.set_error(_('wrong answer'))

    def get_title(self):
        return self.question

    def render_content(self):
        r = TemplateIO(html=True)
        for widget in self.get_widgets():
            r += widget.render_content()
        return r.getvalue()


class WidgetList(quixote.form.widget.WidgetList):
    always_include_add_button = False

    def __init__(
        self,
        name,
        value=None,
        element_type=StringWidget,
        element_kwargs=None,
        add_element_label='Add row',
        default_items_count=None,
        max_items=None,
        **kwargs,
    ):
        if add_element_label == 'Add row':
            add_element_label = str(_('Add row'))

        self.extra_css_class = kwargs.pop('extra_css_class', None)

        CompositeWidget.__init__(self, name, value=value, **kwargs)
        self.element_type = element_type
        self.element_kwargs = element_kwargs or {}
        self.element_names = []
        self.default_items_count = default_items_count or 1
        self.max_items = max_items

        # Add element widgets for initial value
        if value is not None:
            for element_value in value:
                self.add_element(value=element_value)
        if not self.element_names:
            # Add at least an element widget
            self.add_element()

        if not kwargs.get('readonly'):
            # add element widgets to match submitted list
            prefix = '%s$element' % self.name
            if get_request().form:
                known_prefixes = {
                    x.split('$', 2)[1] for x in get_request().form.keys() if x.startswith(prefix)
                }
                for dummy in range(len(known_prefixes) - len(self.element_names)):
                    self.add_element()

            # Add submit to add more element widgets
            current_len = len(self.element_names)
            if self.always_include_add_button or (not max_items) or current_len < max_items:
                self.add(
                    SubmitWidget,
                    'add_element',
                    value=add_element_label,
                    render_br=False,
                    extra_css_class='list-add',
                )
            if self.get('add_element') and (not max_items or current_len < max_items):
                # add an empty row
                self.add_element()
            # Add elements until default_items_count
            while len(self.element_names) < self.default_items_count:
                self.add_element()

    def add_element(self, value=None, element_name=None):
        if element_name:
            name = element_name
        else:
            name = 'element%d' % len(self.element_names)
        self.add(self.element_type, name, value=value, index=len(self.element_names), **self.element_kwargs)
        self.element_names.append(name)

    def add_media(self):
        get_response().add_javascript(['jquery.js', 'jquery-ui.js', 'widget_list.js'])

    def transfer_form_value(self, request):
        for widget in self.get_widgets():
            widget.transfer_form_value(request)

    def _parse(self, request):
        super()._parse(request)
        if self.max_items and self.value and len(self.value) > self.max_items:
            self.set_error_code('too_many')

    def get_too_many_message(self):
        return _('Too many elements (maximum: %s)') % self.max_items

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        yield 'too_many'

    def set_value(self, value):
        for dummy in range(len(value) - len(self.element_names)):
            self.add_element()
        for element_name, subvalue in zip(self.element_names, value):
            self.get_widget(element_name).set_value(subvalue)

    def render(self):
        return render(self)

    def render_content(self):
        r = TemplateIO(html=True)
        add_element_widget = self.get_widget('add_element')
        clear_errors = False
        if self.value is None and self.required:
            # if there's no value and it's marked required, there won't be
            # values in subwidgets either, clear them instead of filling the
            # screen with "required field" messages.
            clear_errors = True

        count = 0
        for widget in self.get_widgets():
            if widget is add_element_widget:
                continue
            if clear_errors:
                widget.clear_error()
            r += widget.render()
            count += 1

        if add_element_widget:
            if self.max_items and count >= self.max_items:
                add_element_widget.is_hidden = True
            r += add_element_widget.render()
        return r.getvalue()


class WidgetListOfRoles(WidgetList):
    def __init__(self, name, value=None, roles=None, **kwargs):
        self.first_element_empty_label = kwargs.pop('first_element_empty_label', '---')
        super().__init__(
            name,
            value=value,
            element_type=SingleSelectWidget,
            element_kwargs={
                'render_br': False,
                'options': [(None, '---', '')] + roles or [],
                'attrs': {'data-first-element-empty-label': self.first_element_empty_label},
            },
            **kwargs,
        )


class WidgetDict(quixote.form.widget.WidgetDict):
    # Fix the title and hint setting
    # FIXME: to be fixed in Quixote upstream : title and hint parameters should be removed
    def __init__(
        self,
        name,
        value=None,
        title='',
        hint='',
        *,
        element_key_type=StringWidget,
        element_value_type=StringWidget,
        element_key_kwargs=None,
        element_value_kwargs=None,
        add_element_label='Add row',
        allow_empty_values=False,
        value_for_empty_value=None,
        **kwargs,
    ):
        # noqa pylint: disable=too-many-arguments

        if add_element_label == 'Add row':
            add_element_label = str(_('Add row'))

        super().__init__(
            name,
            value,
            element_key_type=element_key_type,
            element_value_type=element_value_type,
            element_key_kwargs=element_key_kwargs or {},
            element_value_kwargs=element_value_kwargs or {},
            add_element_label=add_element_label,
            **kwargs,
        )
        if title:
            self.title = title
        if hint:
            self.hint = hint
        self.allow_empty_values = allow_empty_values
        self.value_for_empty_value = value_for_empty_value
        del self._names['add_element']
        self.add(
            SubmitWidget,
            'add_element',
            value=add_element_label,
            render_br=False,
        )

    def add_media(self):
        get_response().add_javascript(['widget_list.js'])

    def render_content(self):
        r = TemplateIO(html=True)

        lines = []
        for name in self.element_names:
            if name in ('add_element', 'added_elements'):
                continue
            key_widget = self.get_widget(name + 'key')
            value_widget = self.get_widget(name + 'value')
            lines.append({'key': key_widget, 'value': value_widget})

        def sort_key(line):
            if not isinstance(line['key'], StringWidget) or not line['key'].value:
                return (1, None)  # empty keys always at the end
            return (0, line['key'].value)

        lines.sort(key=sort_key)

        for line in lines:
            r += htmltext(
                '<div class="widget-dict--row">'
                '<div class="dict-key">%s</div>'
                '<div class="dict-separator">: </div>'
                '<div class="dict-value">%s</div>'
                '</div>'
            ) % (line['key'].render(), line['value'].render())
            r += htmltext('\n')
        add_element_widget = self.get_widget('add_element')
        add_element_widget.render_br = False
        add_element_widget.extra_css_class = 'list-add'
        r += add_element_widget.render()
        r += self.get_widget('added_elements').render()
        return r.getvalue()

    def _parse(self, request):
        values = {}
        for name in self.element_names:
            key = self.get(name + 'key')
            value = self.get(name + 'value')
            if key and value:
                values[key] = value
            elif key and self.allow_empty_values:
                values[key] = self.value_for_empty_value
        self.value = values or None


class WysiwygTextWidget(TextWidget):
    ALL_TAGS = [
        'a',
        'abbr',
        'acronym',
        'address',
        'area',
        'article',
        'aside',
        'audio',
        'b',
        'big',
        'blockquote',
        'br',
        'button',
        'canvas',
        'caption',
        'center',
        'cite',
        'code',
        'col',
        'colgroup',
        'command',
        'datagrid',
        'datalist',
        'dd',
        'del',
        'details',
        'dfn',
        'dialog',
        'dir',
        'div',
        'dl',
        'dt',
        'em',
        'event-source',
        'fieldset',
        'figcaption',
        'figure',
        'font',
        'footer',
        'form',
        'h1',
        'h2',
        'h3',
        'h4',
        'h5',
        'h6',
        'header',
        'hr',
        'i',
        'img',
        'input',
        'ins',
        'kbd',
        'keygen',
        'label',
        'legend',
        'li',
        'm',
        'map',
        'menu',
        'meter',
        'multicol',
        'nav',
        'nextid',
        'noscript',
        'ol',
        'optgroup',
        'option',
        'output',
        'p',
        'pre',
        'progress',
        'q',
        's',
        'samp',
        'section',
        'select',
        'small',
        'sound',
        'source',
        'spacer',
        'span',
        'strike',
        'strong',
        'sub',
        'summary',
        'sup',
        'table',
        'tbody',
        'td',
        'textarea',
        'tfoot',
        'th',
        'thead',
        'time',
        'tr',
        'tt',
        'u',
        'ul',
        'var',
        'video',
    ]
    ALL_ATTRS = [
        'abbr',
        'accept',
        'accept-charset',
        'accesskey',
        'action',
        'align',
        'alt',
        'aria-label',
        'aria-level',
        'autocomplete',
        'autofocus',
        'axis',
        'background',
        'balance',
        'bgcolor',
        'bgproperties',
        'border',
        'bordercolor',
        'bordercolordark',
        'bordercolorlight',
        'bottompadding',
        'cellpadding',
        'cellspacing',
        'ch',
        'challenge',
        'char',
        'charoff',
        'charset',
        'checked',
        'choff',
        'cite',
        'class',
        'clear',
        'color',
        'cols',
        'colspan',
        'compact',
        'contenteditable',
        'controls',
        'coords',
        'data',
        'datafld',
        'datapagesize',
        'datasrc',
        'datetime',
        'default',
        'delay',
        'dir',
        'disabled',
        'draggable',
        'dynsrc',
        'enctype',
        'end',
        'face',
        'for',
        'form',
        'frame',
        'galleryimg',
        'gutter',
        'headers',
        'height',
        'hidden',
        'hidefocus',
        'high',
        'href',
        'hreflang',
        'hspace',
        'icon',
        'id',
        'inputmode',
        'ismap',
        'keytype',
        'label',
        'lang',
        'leftspacing',
        'list',
        'longdesc',
        'loop',
        'loopcount',
        'loopend',
        'loopstart',
        'low',
        'lowsrc',
        'max',
        'maxlength',
        'media',
        'method',
        'min',
        'multiple',
        'name',
        'nohref',
        'noshade',
        'nowrap',
        'open',
        'optimum',
        'pattern',
        'ping',
        'point-size',
        'poster',
        'pqg',
        'preload',
        'prompt',
        'radiogroup',
        'readonly',
        'rel',
        'repeat-max',
        'repeat-min',
        'replace',
        'required',
        'rev',
        'rightspacing',
        'role',
        'rows',
        'rowspan',
        'rules',
        'scope',
        'selected',
        'shape',
        'size',
        'span',
        'src',
        'start',
        'step',
        'style',
        'summary',
        'suppress',
        'tabindex',
        'target',
        'template',
        'title',
        'toppadding',
        'type',
        'unselectable',
        'urn',
        'usemap',
        'valign',
        'value',
        'variable',
        'volume',
        'vrml',
        'vspace',
        'width',
        'wrap',
        'xml:lang',
    ]
    ALL_STYLES = [
        'azimuth',
        'background-color',
        'border-bottom-color',
        'border-bottom-left-radius',
        'border-bottom-right-radius',
        'border-collapse',
        'border-color',
        'border-left-color',
        'border-radius',
        'border-right-color',
        'border-top-color',
        'border-top-left-radius',
        'border-top-right-radius',
        'clear',
        'color',
        'cursor',
        'direction',
        'display',
        'elevation',
        'float',
        'font',
        'font-family',
        'font-size',
        'font-style',
        'font-variant',
        'font-weight',
        'height',
        'letter-spacing',
        'line-height',
        'margin',
        'margin-bottom',
        'margin-left',
        'margin-right',
        'margin-top',
        'overflow',
        'padding',
        'padding-bottom',
        'padding-left',
        'padding-right',
        'padding-top',
        'pause',
        'pause-after',
        'pause-before',
        'pitch',
        'pitch-range',
        'richness',
        'speak',
        'speak-header',
        'speak-numeral',
        'speak-punctuation',
        'speech-rate',
        'stress',
        'text-align',
        'text-decoration',
        'text-indent',
        'unicode-bidi',
        'vertical-align',
        'voice-family',
        'volume',
        'white-space',
        'width',
    ]
    TLDS = [x for x in linkifier.TLDS if x != 'id']
    URL_RE = linkifier.build_url_re(tlds=TLDS)
    EMAIL_RE = linkifier.build_email_re(tlds=TLDS)

    def get_plain_text_value(self):
        return misc.html2text(self.value)

    def clean_html(self, value):
        cleaner = Cleaner(
            tags=getattr(self, 'allowed_tags', None) or self.ALL_TAGS,
            css_sanitizer=CSSSanitizer(allowed_css_properties=self.ALL_STYLES),
            attributes=self.ALL_ATTRS,
            strip=True,
            strip_comments=False,
            filters=[
                partial(
                    linkifier.LinkifyFilter,
                    skip_tags=['pre'],
                    parse_email=True,
                    url_re=self.URL_RE,
                    email_re=self.EMAIL_RE,
                )
            ],
        )
        value = cleaner.clean(value).removeprefix('<br />').removesuffix('<br />')
        if not strip_tags(value).strip() and not ('<img' in value or '<hr' in value):
            value = ''
        return value

    def _parse(self, request):
        TextWidget._parse(self, request, use_validation_function=False)
        if self.value:
            self.allowed_tags = self.ALL_TAGS[:]
            if get_publisher().get_site_option('ckeditor-allow-style-tag'):
                self.allowed_tags.append('style')
            if get_publisher().get_site_option('ckeditor-allow-script-tag'):
                self.allowed_tags.append('script')

            self.value = self.clean_html(self.value)

            # unescape Django template tags
            def unquote_django(matchobj):
                return force_str(html.unescape(matchobj.group(0)))

            self.value = re.sub('{[{%](.*?)[%}]}', unquote_django, self.value)
            if self.validation_function:
                try:
                    self.validation_function(self.value)
                except ValueError as e:
                    self.set_error(str(e))
        if self.value == '':
            self.value = None

    def add_media(self):
        get_response().add_javascript(['qommon.wysiwyg.js'])

    def render_content(self):
        from ckeditor.widgets import DEFAULT_CONFIG as CKEDITOR_DEFAULT_CONFIG

        attrs = self.attrs.copy()
        config = copy.deepcopy(CKEDITOR_DEFAULT_CONFIG)
        config.update(settings.CKEDITOR_CONFIGS['default'])
        attrs['data-config'] = json.dumps(config)
        return (
            htmltag('textarea', name=self.name, **attrs)
            + htmlescape(self.value or '')
            + htmltext('</textarea>')
        )


class MiniRichTextWidget(WysiwygTextWidget):
    template_name = 'qommon/forms/widgets/mini-rich-text.html'

    ALL_TAGS = ['p', 'b', 'strong', 'i', 'em', 'br', 'a']
    ALL_ATTRS = ['href']
    ALL_STYLES = []
    EDITION_MODE = 'basic'

    def add_media(self):
        get_response().add_css_include('../xstatic/css/godo.css')


class RichTextWidget(MiniRichTextWidget):
    ALL_TAGS = ['p', 'b', 'strong', 'i', 'em', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'li', 'a']
    EDITION_MODE = 'full'


class TableWidget(CompositeWidget):
    readonly = False

    def __init__(self, name, value=None, rows=None, columns=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.rows = rows
        self.columns = columns

        if 'title' in kwargs:
            del kwargs['title']

        if kwargs.get('readonly'):
            self.readonly = True

        for i in range(len(rows)):
            for j in range(len(columns)):
                widget = self.add_widget(kwargs, i, j)
                widget = self.get_widget('c-%s-%s' % (i, j))
                if value and self.readonly:
                    try:
                        widget.set_value(value[i][j])
                    except IndexError:
                        # somehow the value didn't have the given cell, this is probably
                        # because the field rows/columns have changed since the data was
                        # saved, ignore.
                        pass
                    widget.transfer_form_value(get_request())

    def add_widget(self, kwargs, i, j):
        widget_kwargs = {}
        if kwargs.get('readonly'):
            widget_kwargs['readonly'] = 'readonly'
        return self.add(StringWidget, 'c-%s-%s' % (i, j), **widget_kwargs)

    def render_content(self):
        r = TemplateIO(html=True)
        attrs = copy.copy(self.attrs)
        attrs['aria-labelledby'] = 'form_label_%s' % self.get_name_for_id()
        r += htmltag('table', **attrs)
        r += htmltext('<thead><tr><td></td>')
        for column in self.columns:
            r += htmltext('<th scope="col"><span>%s</span></th>') % column
        r += htmltext('</tr></thead><tbody>')
        for i, row in enumerate(self.rows):
            r += htmltext('<tr><th scope="row">%s</th>') % row
            for j, column in enumerate(self.columns):
                widget = self.get_widget('c-%s-%s' % (i, j))
                r += htmltext('<td>')
                r += widget.render_content()
                r += htmltext('</td>')
            r += htmltext('</tr>')
        r += htmltext('</tbody></table>')
        return r.getvalue()

    def parse(self, request=None):
        CompositeWidget.parse(self, request=request)
        if request is None:
            request = get_request()
        if request.get_method() == 'POST' and self.required:
            if not self.value:
                self.set_error(self.REQUIRED_ERROR)
            else:
                for row in self.value:
                    for column in row:
                        if column:
                            break
                    else:
                        continue
                    break
                else:
                    self.set_error(self.REQUIRED_ERROR)
        return self.value

    def _parse(self, request):
        if self.readonly:
            return
        table = []
        for i in range(len(self.rows)):
            row = []
            for j in range(len(self.columns)):
                widget = self.get_widget('c-%s-%s' % (i, j))
                row.append(widget.parse())
            table.append(row)
        self.value = table

    def set_value(self, value):
        self.value = value
        if not value:
            return
        for i in range(len(self.rows)):
            for j in range(len(self.columns)):
                widget = self.get_widget('c-%s-%s' % (i, j))
                try:
                    widget.set_value(value[i][j])
                except IndexError:
                    pass


class SingleSelectTableWidget(TableWidget):
    def add_widget(self, kwargs, i, j):
        widget_kwargs = {'options': kwargs.get('options')}
        if kwargs.get('readonly'):
            widget_kwargs['readonly'] = 'readonly'
        return self.add(SingleSelectWidget, 'c-%s-%s' % (i, j), **widget_kwargs)


class CheckboxesTableWidget(TableWidget):
    def add_widget(self, kwargs, i, j):
        widget_kwargs = {'options': kwargs.get('options')}
        if kwargs.get('readonly'):
            widget_kwargs['readonly'] = 'readonly'
        return self.add(CheckboxWidget, 'c-%s-%s' % (i, j), **widget_kwargs)


class SingleSelectHintWidget(SingleSelectWidget):
    template_name = 'qommon/forms/widgets/select.html'
    readonly = None

    def __init__(self, name, value=None, **kwargs):
        self.options_with_attributes = kwargs.pop('options_with_attributes', None)
        self.select2 = kwargs.pop('select2', None)
        self.use_hint_as_first_option = kwargs.pop('use_hint_as_first_option', False)
        if 'template-name' in kwargs:
            self.template_name = kwargs.pop('template-name')
        hint = kwargs.pop('hint', None)
        if self.use_hint_as_first_option and len(hint or '') > 80:
            self.use_hint_as_first_option = False
        if self.use_hint_as_first_option:
            hint = html.unescape(strip_tags(hint).strip())
        super().__init__(name, value=value, hint=hint, **kwargs)

    def add_media(self):
        if self.select2:
            get_response().add_javascript(['select2.js'])

    def separate_hint(self):
        return not (self.use_hint_as_first_option)

    def get_options(self):
        if self.options_with_attributes:
            options = self.options_with_attributes[:]
        else:
            options = self.options[:]
        if options[0][0] is None:
            options = self.options[1:]

        group_by = None
        for option in options:
            object, description, key = option[:3]
            html_attrs = {}
            html_attrs['value'] = key
            if self.is_selected(object):
                html_attrs['selected'] = 'selected'
            elif self.readonly and self.value:
                # if readonly only include the selected option
                continue
            if self.options_with_attributes and option[-1].get('disabled'):
                html_attrs['disabled'] = 'disabled'
            if self.options_with_attributes and option[-1].get('group_by'):
                if option[-1].get('group_by') != group_by:
                    if group_by:
                        yield {'end_optgroup': group_by}
                    yield {'start_optgroup': option[-1].get('group_by')}
                group_by = option[-1].get('group_by')
            if description is None:
                description = ''
            yield {
                'description': description,
                'attrs': html_attrs,
                'options': option[-1] if self.options_with_attributes else None,
            }
        if group_by:
            yield {'end_optgroup': group_by}

    def has_valid_options(self):
        # helper function for templates, return True if there's at least a
        # valid option.
        for option in self.get_options():
            if not option['attrs'].get('disabled'):
                return True
        return False

    def get_hint(self):
        if self.separate_hint():
            return SingleSelectWidget.get_hint(self)
        return None


class MultiSelectWidget(MultipleSelectWidget):
    template_name = 'qommon/forms/widgets/multiselect.html'

    def __init__(self, name, value=None, **kwargs):
        self.options_with_attributes = kwargs.pop('options_with_attributes', None)
        self.readonly = bool(kwargs.pop('readonly', False))
        self.min_choices = int(kwargs.pop('min_choices', 0) or 0)
        self.max_choices = int(kwargs.pop('max_choices', 0) or 0)
        try:
            super().__init__(name, value=value, **kwargs)
        except ValueError:
            # ignore ValueError quixote will raise when options are empty.
            if kwargs.get('options'):
                raise

    def add_media(self):
        if not self.readonly:
            get_response().add_javascript(['select2.js'])

    def get_options(self):
        options = self.options_with_attributes or self.options
        for option in options:
            object, description, key = option[:3]
            yield {
                'value': key,
                'label': description,
                'disabled': bool(self.options_with_attributes and option[-1].get('disabled')),
                'selected': self.is_selected(object),
            }

    def get_selected_options_labels(self):
        return list(x.get('label') for x in self.get_options() if x.get('selected'))

    def set_value(self, value):
        if isinstance(value, str) and '|' in value:
            value = value.split('|')
        super().set_value(value)

    def transfer_form_value(self, request):
        options = {x['label']: x['value'] for x in self.get_options()}
        request.form[self.name + '[]'] = [options.get(x, x) for x in (self.value or [])]

    def _parse(self, request):
        orig_name, self.name = self.name, self.name + '[]'
        try:
            super()._parse(request)
        finally:
            self.name = orig_name
        if self.value and self.min_choices and len(self.value) < self.min_choices:
            self.set_error_code('too_short')
        if self.value and self.max_choices and len(self.value) > self.max_choices:
            self.set_error_code('too_long')

    def get_too_short_message(self):
        return ngettext(
            'You must select at least %(min_choices)d choice.',
            'You must select at least %(min_choices)d choices.',
            self.min_choices,
        ) % {'min_choices': self.min_choices}

    def get_too_long_message(self):
        return ngettext(
            'You must select at most %(max_choices)d choice.',
            'You must select at most %(max_choices)d choices.',
            self.max_choices,
        ) % {'max_choices': self.max_choices}

    def get_error_message_codes(self):
        yield from super().get_error_message_codes()
        yield 'too_short'
        yield 'too_long'


class WidgetListAsTable(WidgetList):
    def render_content(self):
        r = TemplateIO(html=True)
        add_element_widget = self.get_widget('add_element')
        if add_element_widget:
            add_element_widget.render_br = False
            add_element_widget.extra_css_class = 'list-add'
        for widget in self.get_widgets():
            if widget is add_element_widget:
                continue
            if not hasattr(widget, 'render_content_as_tr'):
                r += widget.render()
        attrs = copy.copy(self.attrs)
        attrs['aria-labelledby'] = 'form_label_%s' % self.get_name_for_id()
        r += htmltag('table', **attrs)
        r += htmltext('<thead>')
        r += self.get_widgets()[0].render_as_thead()
        r += htmltext('</thead>')
        r += htmltext('<tbody>')
        for widget in self.get_widgets():
            if widget is add_element_widget:
                continue
            if hasattr(widget, 'render_content_as_tr'):
                r += widget.render_content_as_tr()
        r += htmltext('</tbody>')
        r += htmltext('</table>')
        if add_element_widget and not self.readonly:
            r += add_element_widget.render()
        return r.getvalue()

    def render(self):
        return render(self)


class TableRowWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        for i, column in enumerate(self.columns):
            self.add(StringWidget, name='col%s' % i, title=column, **kwargs)


class TableListRowsWidget(WidgetListAsTable):
    readonly = False

    def create_row_class(self, columns):
        class klass(TableRowWidget):
            pass

        setattr(klass, 'columns', columns)
        return klass

    def add_element(self, value=None):
        name = 'element%d' % len(self.element_names)
        self.add(self.table_row_class, name, value=value, **self.widget_kwargs)
        self.element_names.append(name)

    def __init__(self, name, value=None, columns=None, min_rows=5, **kwargs):
        self.table_row_class = self.create_row_class(columns)
        self.widget_kwargs = {}
        if 'readonly' in kwargs:
            del kwargs['readonly']
            self.readonly = True
            self.widget_kwargs['readonly'] = 'readonly'
        WidgetListAsTable.__init__(
            self, name, value, element_type=self.table_row_class, element_kwargs=self.widget_kwargs, **kwargs
        )
        self.columns = columns

        while len(self.element_names) < min_rows:
            self.add_element()

        self.set_value(value)

    def parse(self, request=None):
        WidgetListAsTable.parse(self, request=request)
        if request is None:
            request = get_request()
        add_element_pushed = self.get_widget('add_element').parse()
        if request.get_method() == 'POST' and self.required:
            if not self.value and not add_element_pushed:
                self.set_error(self.REQUIRED_ERROR)
            for row in self.value or []:
                for column in row:
                    if column:
                        break
                else:
                    continue
                break
            else:
                if not add_element_pushed:
                    self.set_error(self.REQUIRED_ERROR)
        return self.value

    def _parse(self, request):
        if self.readonly:
            return
        table = []
        for row_name in self.element_names:
            row = []
            row_widget = self.get_widget(row_name)
            notnull = False
            for j in range(len(self.columns)):
                widget = row_widget.get_widget('col%s' % j)
                row.append(widget.parse())
                if row[-1]:
                    notnull = True
            if notnull:
                table.append(row)
        self.value = table

    def set_value(self, value):
        self.value = value
        if not value:
            return
        while len(self.element_names) < len(value):
            self.add_element()
        for i, row_name in enumerate(self.element_names):
            widget_row = self.get_widget(row_name)
            for j in range(len(self.columns)):
                widget = widget_row.get_widget('col%s' % j)
                try:
                    widget.set_value(value[i][j])
                    widget.transfer_form_value(get_request())
                except IndexError:
                    pass


class RankedItemsWidget(CompositeWidget):
    readonly = False
    has_inside_labels = True

    def __init__(self, name, value=None, elements=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.element_names = {}

        if 'title' in kwargs:
            del kwargs['title']
        if 'readonly' in kwargs:
            if kwargs['readonly']:
                self.readonly = True
            del kwargs['readonly']
        if 'required' in kwargs:
            if kwargs['required']:
                self.required = True
            del kwargs['required']

        self.randomize_items = False
        if 'randomize_items' in kwargs:
            if kwargs['randomize_items']:
                self.randomize_items = True
            del kwargs['randomize_items']

        for v in elements:
            if isinstance(v, tuple):
                title = v[1]
                key = v[0]
                if isinstance(key, int):
                    name = 'element%d' % v[0]
                elif type(key) in (str, htmltext):
                    name = str('element%s' % v[0])
                    key = str(key)
                else:
                    raise NotImplementedError()
            else:
                title = v
                key = v
                name = 'element%d' % len(self.element_names.keys())

            if value:
                position = value.get(key)
            else:
                position = None
            self.add(IntWidget, name, title=title, value=position, size=5, required=False, **kwargs)
            self.element_names[name] = key

        if self.randomize_items:
            random.shuffle(self.widgets)

        if self.readonly:
            self.widgets.sort(key=lambda x: x.value or sys.maxsize)

            if value:
                # in readonly mode, we mark all fields as already parsed, to
                # avoid getting them reinitialized on a has_error() call
                for name in self.element_names:
                    self.get_widget(name)._parsed = True
                self._parsed = True

    def _parse(self, request):
        values = {}
        for key, val in self.element_names.items():
            value = self.get(key)
            if value is not None:
                values[val] = value
            if value is not None and not isinstance(value, int):
                self.get_widget(key).set_error_code('type_mismatch')
        self.value = values or None

    def get_type_mismatch_message(self):
        return _('must be a number')

    def set_value(self, value):
        self.value = value
        if value:
            for key, val in self.element_names.items():
                self.get_widget(key).set_value(self.value.get(val))

    def render_content(self):
        r = TemplateIO(html=True)
        r += htmltext('<ul>')
        for widget in self.get_widgets():
            if widget.has_error():
                r += htmltext('<li class="error"><label>')
            else:
                r += htmltext('<li><label>')
            if self.readonly:
                widget.attrs['disabled'] = 'disabled'
                if widget.value:
                    r += htmltext('<input type="hidden" name="%s" value="%s" >') % (widget.name, widget.value)
                widget.name = widget.name + 'xx'
            r += widget.render_content()
            r += widget.title
            r += htmltext('</label>')
            r += htmltext('</li>')
        r += htmltext('</ul>')
        return r.getvalue()


class JsonpSingleSelectWidget(Widget):
    template_name = 'qommon/forms/widgets/select_jsonp.html'
    prefill_attributes = None

    def __init__(self, name, value=None, url=None, add_related_url=None, with_related=False, **kwargs):
        self.url = url
        self.add_related_url = add_related_url
        self.with_related = with_related
        hint = kwargs.pop('hint', None)
        self.use_hint_as_first_option = kwargs.pop('use_hint_as_first_option', False)
        if self.use_hint_as_first_option and len(hint or '') > 80:
            self.use_hint_as_first_option = False
        if self.use_hint_as_first_option:
            hint = html.unescape(strip_tags(hint).strip())
        super().__init__(name, value=value, hint=hint, **kwargs)

    def separate_hint(self):
        return not (self.use_hint_as_first_option)

    def get_hint(self):
        if self.separate_hint():
            return super().get_hint()
        return None

    def add_media(self):
        get_response().add_javascript(['select2.js'])
        if self.prefill_attributes and 'geolocation' in self.prefill_attributes:
            get_response().add_javascript(['qommon.geolocation.js'])

    def get_display_value(self):
        if self.value is None:
            value = None
        else:
            value = htmlescape(self.value)
        if not value:
            return None
        key = '%s_%s' % (self.url, value)
        value = get_session().get_jsonp_display_value(key)
        if value:
            return value
        # get display value from data source; if it works it will be put in
        # jsonp_display_values as a side effect.
        field = getattr(self, 'field', None)
        if field:
            return field.get_display_value(self.value)

    def _get_carddata(self):
        if self.value is None:
            value = None
        else:
            value = htmlescape(self.value)
        if not value:
            return None
        field = getattr(self, 'field', None)
        if not field:
            return
        carddef = field.get_carddef()
        if not carddef:
            return
        try:
            return carddef.data_class().get_by_id(value)
        except KeyError:
            return

    def get_edit_related_url(self):
        if not self.with_related:
            return
        carddata = self._get_carddata()
        if not carddata:
            return
        return carddata.get_edit_related_url()

    def get_view_related_url(self):
        if not self.with_related:
            return
        carddata = self._get_carddata()
        if not carddata:
            return
        return carddata.get_view_related_url()

    def get_select2_url(self):
        if Template.is_template_string(self.url):
            vars = get_publisher().substitutions.get_context_variables(mode='lazy')
            # skip variables that were not set (None)
            vars = {x: y for x, y in vars.items() if y is not None}
            url = misc.get_variadic_url(self.url, vars, encode_query=False)
        else:
            url = self.url
        return url

    def parse(self, request=None):
        if request and request.form.get(self.name) and request.form.get(self.name + '_display'):
            # store text value associated to the jsonp value
            value = request.form.get(self.name)
            display_value = request.form.get(self.name + '_display')
            get_session().set_jsonp_display_value('%s_%s' % (self.url, value), display_value)

        return Widget.parse(self, request=request)


class AutocompleteStringWidget(WcsExtraStringWidget):
    url = None

    def __init__(self, *args, **kwargs):
        self.url = kwargs.pop('url', None)
        WcsExtraStringWidget.__init__(self, *args, **kwargs)

    def add_media(self):
        super().add_media()
        get_response().add_javascript(['jquery.js', 'jquery-ui.js'])

    def render_content(self):
        if Template.is_template_string(self.url):
            vars = get_publisher().substitutions.get_context_variables(mode='lazy')
            # skip variables that were not set (None)
            vars = {x: y for x, y in vars.items() if y is not None}
            url = misc.get_variadic_url(self.url, vars, encode_query=False)
        else:
            url = self.url

        r = TemplateIO(html=True)
        r += WcsExtraStringWidget.render_content(self)
        if not url:
            # there's no autocomplete URL, get out now.
            return r.getvalue()

        data_type = 'json' if url.startswith('/api/autocomplete/') else 'jsonp'
        r += htmltext(
            """
<script id="script_%(id)s">
$(function() {
  $("#form_%(id)s").autocomplete({
    source: function( request, response ) {
      $.ajax({
        url: $("#form_%(id)s").data('uiAutocomplete').options.url,
        dataType: "%(data_type)s",
        data: {
          q: request.term
        },
        success: function( data ) {
          response( $.map(data.data, function(item) {
            return {label: item.text, value: item.label};
           }));
        }
      });
    },
    minLength: 2,
    open: function() {
      $(this).removeClass("ui-corner-all").addClass("ui-corner-top");
    },
    close: function() {
      $(this).removeClass("ui-corner-top").addClass("ui-corner-all");
    },
    messages: {
      noResults: %(no_search_results)s,
      results: function(amount) {
                 return amount + " " + ( amount > 1 ? %(results_are_available)s : %(result_is_available)s ) +
                 ", " + %(up_up_and_down)s;
      }
    }
  });
"""
            % {
                'id': self.get_name_for_id(),
                'data_type': data_type,
                'no_search_results': json.dumps(str(_('No search results.'))),
                'results_are_available': json.dumps(str(_('results are available'))),
                'result_is_available': json.dumps(str(_('result is available'))),
                'up_up_and_down': json.dumps(str(_('use up and down arrow keys to navigate.'))),
            }
        )

        if '[var_' not in url:
            r += htmltext(
                """
$("#form_%(id)s").data('uiAutocomplete').options.url = '%(url)s';
"""
                % {'id': self.get_name_for_id(), 'url': url}
            )

        if '[var_' in url:
            # if this is a parametric url, store template url and hook to the
            # appropriate onchange event to give the url to autocomplete
            r += htmltext(
                """
$("#form_%(id)s").data('uiAutocomplete').options.wcs_base_url = '%(url)s';
"""
                % {'id': self.get_name_for_id(), 'url': url}
            )
            variables = re.findall(r'\[(var_.+?)\]', url)
            r += htmltext(
                """
function url_replace_%(id)s() {
    var url = $("#form_%(id)s").data('uiAutocomplete').options.wcs_base_url;"""
                % {'id': self.get_name_for_id()}
            )
            for variable in variables:
                r += htmltext(
                    """
    selector = '#' + $('#%(variable)s').data('valuecontainerid');
    url = url.replace('[%(variable)s]', $(selector).val() || '');"""
                    % {'variable': variable}
                )
            r += htmltext(
                """
    $("#form_%(id)s").data('uiAutocomplete').options.url = url;
    if ($("form_%(id)s").val() != $("form_%(id)s").attr('value'))
        $("#form_%(id)s").val('');
}
"""
                % {'id': self.get_name_for_id()}
            )
            for variable in variables:
                r += htmltext(
                    """
$('#%(variable)s').change(url_replace_%(id)s);
$('#%(variable)s').change();
"""
                    % {'id': self.get_name_for_id(), 'variable': variable}
                )

        r += htmltext(
            """
});
</script>"""
        )

        return r.getvalue()


class ColourWidget(StringWidget):
    HTML_TYPE = 'color'


class PasswordEntryWidget(CompositeWidget):
    min_length = 0
    max_length = 0
    count_uppercase = 0
    count_lowercase = 0
    count_digit = 0
    count_special = 0
    confirmation = True

    def __init__(self, name, value=None, **kwargs):
        # hint will be displayed with pwd1 widget
        hint = kwargs.pop('hint', None)
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.min_length = kwargs.get('min_length', 0)
        self.max_length = kwargs.get('max_length', 0)
        self.count_uppercase = kwargs.get('count_uppercase', 0)
        self.count_lowercase = kwargs.get('count_lowercase', 0)
        self.count_digit = kwargs.get('count_digit', 0)
        self.count_special = kwargs.get('count_special', 0)
        self.confirmation = kwargs.get('confirmation', True)
        confirmation_title = kwargs.get('confirmation_title') or _('Confirmation')
        self.strength_indicator = kwargs.get('strength_indicator', True)

        self.formats = kwargs.get('formats', ['sha1'])
        if not self.attrs.get('readonly'):
            self.add(
                PasswordWidget,
                name='pwd1',
                title='',
                value='',
                required=kwargs.get('required', False),
                autocomplete='off',
                hint=hint,
                attrs={'id': 'form_' + self.get_name_for_id()},
            )
            if self.confirmation:
                self.add(
                    PasswordWidget,
                    name='pwd2',
                    title=confirmation_title,
                    required=kwargs.get('required', False),
                    autocomplete='off',
                )
        else:
            encoded_value = force_str(base64.encodebytes(force_bytes(json.dumps(value))))
            if value:
                fake_value = '*' * 8
            else:
                fake_value = ''
            self.add(
                HtmlWidget,
                'hashed',
                title=htmltext(
                    '<input value="%s" readonly="readonly">'
                    '<input type="hidden" name="%s$encoded" value="%s"></input>'
                    % (fake_value, self.name, encoded_value)
                ),
            )

    def add_media(self):
        get_response().add_javascript(['jquery.js', 'jquery.passstrength.js'])

    def render_content(self):
        if self.attrs.get('readonly') or not self.strength_indicator:
            return CompositeWidget.render_content(self)
        r = TemplateIO(html=True)
        r += CompositeWidget.render_content(self)
        ctx = {
            'form_id': self.get_name_for_id(),
            'min_length': self.min_length,
            'veryweak': _('Very weak'),
            'weak': _('Weak'),
            'moderate': _('Moderate'),
            'good': _('Good'),
            'strong': _('Strong'),
            'verystrong': _('Very strong'),
            'password_strength': _('Password strength:'),
            'tooshort': _('Too short'),
        }
        r += (
            htmltext(
                '''<script>
$(function() {
  $('input[id="form_%(form_id)s"]').passStrengthify({
    levels: ["%(veryweak)s", "%(veryweak)s", "%(weak)s", "%(weak)s", "%(moderate)s", "%(good)s", "%(strong)s", "%(verystrong)s"],
    minimum: %(min_length)s,
    labels: {
      passwordStrength: "%(password_strength)s",
      tooShort: "%(tooshort)s"
    }
  });
});
</script>'''
            )
            % ctx
        )
        return r.getvalue()

    def _parse(self, request):
        CompositeWidget._parse(self, request)
        if request.form.get('%s$encoded' % self.name):
            self.value = json.loads(
                base64.decodebytes(force_bytes(request.form.get('%s$encoded' % self.name)))
            )
            return
        pwd1 = self.get('pwd1') or ''

        if not self.get_widget('pwd1'):
            # we are in read-only mode, stop here.
            return

        set_errors = []
        min_len = self.min_length
        if len(pwd1) < min_len:
            set_errors.append(_('Password is too short.  It must be at least %d characters.') % min_len)

        max_len = self.max_length
        if max_len and len(pwd1) > max_len:
            set_errors.append(_('Password is too long.  It must be at most %d characters.') % max_len)

        count = self.count_uppercase
        if len([x for x in pwd1 if x.isupper()]) < count:
            set_errors.append(
                ngettext(
                    'Password must contain an uppercase character.',
                    'Password must contain at least %(count)d uppercase characters.',
                    count,
                )
                % {'count': count}
            )

        count = self.count_lowercase
        if len([x for x in pwd1 if x.islower()]) < count:
            set_errors.append(
                ngettext(
                    'Password must contain a lowercase character.',
                    'Password must contain at least %(count)d lowercase characters.',
                    count,
                )
                % {'count': count}
            )

        count = self.count_digit
        if len([x for x in pwd1 if misc.is_ascii_digit(x)]) < self.count_digit:
            set_errors.append(
                ngettext(
                    'Password must contain a digit.',
                    'Password must contain at least %(count)d digits.',
                    count,
                )
                % {'count': count}
            )

        count = self.count_special
        if len([x for x in pwd1 if not x.isalnum()]) < count:
            set_errors.append(
                ngettext(
                    'Password must contain a special character.',
                    'Password must contain at least %(count)d special characters.',
                    count,
                )
                % {'count': count}
            )

        if self.confirmation:
            pwd2 = self.get('pwd2') or ''
            if pwd1 != pwd2:
                self.get_widget('pwd2').set_error(_('Passwords do not match.'))
                pwd1 = None

        if set_errors:
            self.get_widget('pwd1').set_error(' '.join(set_errors))
            pwd1 = None

        PASSWORD_FORMATS = {
            'cleartext': force_str,
            'md5': lambda x: force_str(hashlib.md5(force_bytes(x)).hexdigest()),
            'sha1': lambda x: force_str(hashlib.sha1(force_bytes(x)).hexdigest()),
        }

        if pwd1:
            self.value = {}
            for fmt in self.formats:
                self.value[fmt] = PASSWORD_FORMATS[fmt](pwd1)
        else:
            self.value = None


class MapWidget(CompositeWidget):
    template_name = 'qommon/forms/widgets/map.html'

    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        latlng_value = None
        if isinstance(value, str):  # legacy data type
            latlng_value = value
        elif value:
            latlng_value = '%s;%s' % (value['lat'], value['lon'])
        self.add(HiddenWidget, 'latlng', value=latlng_value)
        self.readonly = kwargs.pop('readonly', False)
        self.init_map_attributes(value, **kwargs)

    def init_map_attributes(self, value, **kwargs):
        self.map_attributes = {}
        self.map_attributes.update(get_publisher().get_map_attributes())
        self.map_attributes['data-map-attribution'] = mark_safe(self.map_attributes['data-map-attribution'])
        self.sync_map_and_address_fields = get_publisher().has_site_option('sync-map-and-address-fields')
        if kwargs.get('initial_zoom') is None:
            kwargs['initial_zoom'] = get_publisher().get_default_zoom_level()

        for attribute in ('initial_zoom', 'min_zoom', 'max_zoom'):
            if attribute in kwargs:
                self.map_attributes['data-' + attribute] = kwargs.pop(attribute)
        initial_position = kwargs.pop('initial_position', None)
        default_position = kwargs.pop('default_position', None)
        position_template = kwargs.pop('position_template', None)
        if not value:
            if initial_position == 'geoloc-front-only':
                initial_position = (
                    'point' if (get_request() and get_request().is_in_backoffice()) else 'geoloc'
                )
            if initial_position == 'geoloc':
                self.map_attributes['data-init_with_geoloc'] = 'true'
            elif initial_position == 'point' and default_position:
                if isinstance(default_position, str):
                    self.map_attributes['data-def-lat'] = default_position.split(';')[0]
                    self.map_attributes['data-def-lng'] = default_position.split(';')[1]
                else:
                    self.map_attributes['data-def-lat'] = default_position['lat']
                    self.map_attributes['data-def-lng'] = default_position['lon']
            elif initial_position == 'template' and position_template:
                from wcs.workflows import WorkflowStatusItem

                try:
                    position = WorkflowStatusItem.compute(
                        position_template, raises=True, allow_complex=False, record_errors=False
                    )
                except TemplateError:
                    pass
                else:
                    if re.match(r'-?\d+(\.\d+)?;-?\d+(\.\d+)?$', position):
                        # lat;lon
                        self.map_attributes['data-def-lat'] = position.split(';')[0]
                        self.map_attributes['data-def-lng'] = position.split(';')[1]
                        self.map_attributes['data-def-template'] = 'true'
                    else:
                        # address?
                        from wcs.wf.geolocate import GeolocateWorkflowStatusItem

                        geolocate = GeolocateWorkflowStatusItem()
                        geolocate.method = 'address_string'
                        geolocate.address_string = position
                        coords = geolocate.geolocate_address_string(None, compute_template=False)
                        if coords:
                            self.map_attributes['data-def-lat'] = '%.8f' % coords['lat']
                            self.map_attributes['data-def-lng'] = '%.8f' % coords['lon']
                            self.map_attributes['data-def-template'] = 'true'

    def point2str(self, value):
        if not value:
            return None
        return '%s;%s' % (value['lat'], value['lon'])

    def transfer_form_value(self, request):
        request.form[self.get_widget('latlng').name] = self.point2str(self.value)

    def initial_position(self):
        if isinstance(self.value, str) and ';' in self.value:
            return {'lat': self.value.split(';')[0], 'lng': self.value.split(';')[1]}
        if isinstance(self.value, dict):
            return {'lat': self.value['lat'], 'lng': self.value['lon']}
        return None

    def add_media(self):
        get_response().add_javascript(['qommon.map.js', 'leaflet-search.js'])

    def _parse(self, request):
        CompositeWidget._parse(self, request)
        self.value = self.get('latlng')
        if self.value:
            try:
                lat, lon = self.value.split(';')
            except ValueError:
                self.value = None
                self.set_error_code('bad_input')
            else:
                self.value = misc.normalize_geolocation({'lat': lat, 'lon': lon})

    def set_value(self, value):
        super().set_value(value)
        self.get_widget('latlng').set_value(value)


class MapMarkerSelectionWidget(MapWidget):
    template_name = 'qommon/forms/widgets/map-marker-selection.html'

    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.add(HiddenWidget, 'marker_id', value=value)
        self.marker_id_widget = self.get_widget('marker_id')
        self.readonly = kwargs.pop('readonly', False)

        self.init_map_attributes(value, **kwargs)

        from wcs import data_sources

        data_source = data_sources.get_object(kwargs['data_source'])
        self.geojson_markers_url = data_source.get_geojson_url() if data_source else ''

    def initial_position(self):
        return None

    def transfer_form_value(self, request):
        request.form[self.name] = self.value

    def _parse(self, request):
        CompositeWidget._parse(self, request)
        self.value = self.get('marker_id')

    def set_value(self, value):
        self.value = value
        self.marker_id_widget.set_value(value)


class HiddenErrorWidget(HiddenWidget):
    def set_error(self, error):
        Widget.set_error(self, error)


class SingleSelectWidgetWithOther(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        other_widget_class = kwargs.pop('other_widget_class', StringWidget)
        CompositeWidget.__init__(self, name, value=value, **kwargs)
        kwargs.pop('attrs', None)
        if 'title' in kwargs:
            del kwargs['title']
        options = kwargs.get('options')[:]
        if options:
            if not isinstance(options[0], (tuple, list)):
                options = [(x, x, x) for x in options]
            elif len(options[0]) == 1:
                options = [(x[0], x[0], x[0]) for x in options]
            elif len(options[0]) == 2:
                options = [(x[0], x[1], x[0]) for x in options]
        options.append(('__other', kwargs.pop('other_label', _('Other:')), '__other'))
        kwargs['options'] = options
        if value is None or value in [x[0] for x in options]:
            choice_value = value
            other_value = None
        else:
            choice_value = '__other'
            other_value = value
        self.add(
            SingleSelectWidget,
            'choice',
            value=choice_value,
            attrs={'data-dynamic-display-parent': 'true'},
            **kwargs,
        )
        self.add(
            other_widget_class,
            'other',
            value=other_value,
            size=35,
            attrs={
                'data-dynamic-display-value': '__other',
                'data-dynamic-display-child-of': f'{name}$choice',
            },
        )

    def _parse(self, request):
        self.value = self.get('choice')
        self.has_other_value = bool(self.value == '__other')
        if self.has_other_value:
            self.value = self.get('other')


class ComputedExpressionWidget(CompositeWidget):
    """Widget that checks the entered value is a correct workflow
    expression."""

    def __init__(self, name, value=None, *args, **kwargs):
        if not value:
            value = {}
        else:
            from wcs.workflows import WorkflowStatusItem

            value = WorkflowStatusItem.get_expression(value)
            if value.get('type') == 'text':
                value['type'] = 'template'

        value_placeholder = kwargs.pop('value_placeholder', None)
        CompositeWidget.__init__(self, name, value, **kwargs)

        self.add(
            StringWidget,
            'value_template',
            size=80,
            value=value.get('value') if value.get('type') == 'template' else None,
            placeholder=value_placeholder,
        )

        self.initial_value = value

    def render_content(self):
        ctx = {
            'name': self.name,
            'template_label': _('Template'),
            'value_template': self.get_widget('value_template').render_content(),
        }
        return (
            htmltext(
                '''\
<style>span[data-name="%(name)s"].template::after { content: "%(template_label)s"; }</style>
<span class="template only" data-name="%(name)s">%(value_template)s</span>'''
            )
            % ctx
        )

    @classmethod
    def validate_template(cls, template):
        try:
            Template(template, raises=True)
        except TemplateError as e:
            raise ValidationError('%s' % e)

    @classmethod
    def validate(cls, expression, initial_value=None):
        if not expression:
            return
        from wcs.workflows import WorkflowStatusItem

        expression = WorkflowStatusItem.get_expression(expression)
        cls.validate_template(expression['value'])

    def _parse(self, request):
        self.value = None
        value_type = 'template'
        value_content = self.get('value_%s' % value_type)
        self.value = value_content
        if self.value:
            try:
                self.validate(self.value, initial_value=self.initial_value)
            except ValidationError as e:
                self.set_error(str(e))


class ConditionWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)

        if not value:
            value = {}

        self.add(
            StringWidget,
            'value_django',
            size=80,
            value=value.get('value') if value.get('type') == 'django' else None,
        )

        self.initial_value = value

    @property
    def content_extra_attributes(self):
        validation_url = get_publisher().get_root_url() + 'api/validate-condition'
        return {'data-validation-url': validation_url}

    def _parse(self, request):
        self.value = None
        self.value = {'type': 'django'}

        self.value['value'] = self.get('value_%s' % self.value['type'])

        if self.value['value']:
            try:
                Condition(self.value).validate()
            except ValidationError as e:
                self.set_error(str(e))
        else:
            self.value = None

    def render_content(self):
        ctx = {
            'name': self.name,
            'value_django': self.get_widget('value_django').render_content(),
        }
        return (
            htmltext(
                '<input type="hidden" name="%(name)s$type" value="django">'
                '<span class="django only">%(value_django)s</span>'
            )
            % ctx
        )


class DjangoConditionWidget(StringWidget):
    def _parse(self, request):
        super()._parse(request)
        if self.value:
            try:
                Condition({'type': 'django', 'value': self.value}).validate()
            except ValidationError as e:
                self.set_error(str(e))


class TimeRangeWidget(CompositeWidget):
    template_name = 'qommon/forms/widgets/time-range.html'

    def __init__(
        self,
        name,
        value=None,
        title='',
        hint='',
        required=False,
        options_metadata=None,
        options_with_attributes=None,
        **kwargs,
    ):
        CompositeWidget.__init__(self, name, value=value, title=title, hint=hint, required=required)

        self.id = kwargs['id']
        self.attrs['data-opt-group-label'] = _('Between %(start)s and %(end)s') % {
            'start': '__start__',
            'end': '__end__',
        }

        self.fillslot_url = options_metadata.get('api', {}).get('fillslot_url')
        self.attrs['data-minimal-booking-slots'] = options_metadata.get('minimal_booking_slots')
        self.attrs['data-maximal-booking-slots'] = options_metadata.get('maximal_booking_slots')

        self.hours_by_day = {
            x[-1]['id']: x[-1].get('opening_hours', {}) for x in options_with_attributes if 'id' in x[-1]
        }

        for option in options_with_attributes:
            option[-1]['opening-hours'] = json.dumps(option[-1].get('opening_hours', {}))
            option[-1]['verbose-label'] = option[-1].get('verbose_label')

        options_with_attributes = options_with_attributes or [(None, '---', '', {})]

        self.add(
            RadiobuttonsWidget,
            'day',
            required=required,
            options_with_attributes=options_with_attributes,
            options=[x[0] for x in options_with_attributes],
            list_css_class='bare-list TimeRange--days-list',
            option_data_attributes=('opening-hours', 'verbose-label'),
        )
        widget = self.get_widget('day')
        widget.attrs['class'] = 'sr-only TimeRange--day-radio'

        self.add(
            SingleSelectWidget,
            'start_hour',
            title=_('Start hour'),
            required=required,
            options=[(None, '---', '')],
        )
        self.add(
            SingleSelectWidget,
            'end_hour',
            title=_('End hour'),
            required=required,
            options=[(None, '---', '')],
        )

    def add_media(self):
        get_response().add_javascript(['time-range-widget.js'])
        get_response().add_css_include('time-range-widget.css')

    def get_day_options_json(self):
        widget = self.get_widget('day')
        return [
            {
                'id': option[2],
                'text': option[1],
                'disabled': option[3].get('disabled', False),
                'attributes': option[3],
            }
            for option in widget.options_with_attributes
        ]

    def set_hour_choices(self, day):
        hours = []
        start_hours = []
        end_hours = []
        for hour in self.hours_by_day.get(day, []):
            if hour['status'] == 'free':
                hours.append(hour['hour'])
                continue

            start_hours.extend(hours)
            end_hours.extend(hours[1:])
            end_hours.append(hour['hour'])
            hours.clear()

        start_hour = self.get_widget('start_hour')
        start_hour.set_options(start_hours)

        end_hour = self.get_widget('end_hour')
        end_hour.set_options(end_hours)

    def _parse(self, request):
        day = self.get('day')
        if not day:
            return

        self.set_hour_choices(day)

        start_hour = self.get('start_hour')
        end_hour = self.get('end_hour')
        if not start_hour or not end_hour:
            return

        day = datetime.date.fromisoformat(day)
        start_hour = datetime.time.fromisoformat(start_hour)
        end_hour = datetime.time.fromisoformat(end_hour)

        self.value = {
            'start_datetime': datetime.datetime.combine(day, start_hour).strftime('%Y-%m-%d %H:%M'),
            'end_datetime': datetime.datetime.combine(day, end_hour).strftime('%Y-%m-%d %H:%M'),
            'api': {'fillslot_url': self.fillslot_url},
        }

    def set_value(self, value):
        start_datetime = datetime.datetime.fromisoformat(value['start_datetime'])
        end_datetime = datetime.datetime.fromisoformat(value['end_datetime'])

        day = start_datetime.strftime('%Y-%m-%d')
        self.get_widget('day').set_value(day)

        self.set_hour_choices(day)

        self.get_widget('start_hour').set_value(start_datetime.strftime('%H:%M'))
        self.get_widget('end_hour').set_value(end_datetime.strftime('%H:%M'))


def get_rich_text_widget_class(content, usage):
    # get widget for rich text content, with different fallback modes, to avoid godo.js
    # if all tags in existing content are not supported.
    behaviour = get_publisher().get_site_option(f'rich-text-{usage}')

    tags = set(re.findall(r'<([a-z]+)[\s>]', content or ''))
    has_godo_unsupported_tags = not (tags.issubset(set(RichTextWidget.ALL_TAGS)))
    has_mini_godo_unsupported_tags = not (tags.issubset(set(MiniRichTextWidget.ALL_TAGS)))
    is_django_template = Template.is_template_string(content or '', ezt_support=False)

    if behaviour.startswith('mini-') and not has_mini_godo_unsupported_tags:
        godo_widget = MiniRichTextWidget
    else:
        godo_widget = RichTextWidget

    behaviour = behaviour.removeprefix('mini-')

    if behaviour == 'godo':
        widget = godo_widget
    elif behaviour == 'ckeditor':
        widget = WysiwygTextWidget
    elif behaviour == 'textarea':
        widget = TextWidget
    elif behaviour == 'auto-ckeditor' and (has_godo_unsupported_tags or is_django_template):
        widget = WysiwygTextWidget
    elif behaviour == 'auto-ckeditor-textarea' and is_django_template:
        widget = TextWidget
    elif behaviour == 'auto-ckeditor-textarea' and has_godo_unsupported_tags:
        widget = WysiwygTextWidget
    elif behaviour == 'auto-textarea' and (has_godo_unsupported_tags or is_django_template):
        widget = TextWidget
    else:
        widget = godo_widget
    return widget
