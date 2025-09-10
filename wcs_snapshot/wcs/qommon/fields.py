# w.c.s. - web application for online forms
# Copyright (C) 2005-2025  Entr'ouvert
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

from quixote import get_publisher
from quixote.html import TemplateIO, htmltext

from . import _


def get_summary_field_details(
    filled,
    fields=None,
    include_unset_required_fields=False,
    data=None,
    parent_field=None,
    parent_field_index=None,
    wf_form=False,
):
    if fields is None:
        fields = filled.formdef.fields

    if data is None:
        data = filled.data

    on_page = False
    current_page_fields = []
    pages = []

    latest_title = None
    latest_subtitle = None
    has_contents_since_latest_title = False
    has_contents_since_latest_subtitle = False

    def finish_subtitles(page_fields):
        nonlocal latest_subtitle
        if latest_subtitle and not has_contents_since_latest_subtitle:
            del current_page_fields[page_fields.index(latest_subtitle)]
        latest_subtitle = None

    def finish_titles(page_fields):
        nonlocal latest_title
        finish_subtitles(page_fields)
        if latest_title and not has_contents_since_latest_title:
            del current_page_fields[page_fields.index(latest_title)]
        latest_title = None

    for f in fields:
        if f.key == 'page':
            finish_titles(current_page_fields)
            on_page = f
            current_page_fields = []
            pages.append({'field': f, 'fields': current_page_fields})
            continue

        if f.key == 'title' and on_page and not current_page_fields and on_page.label == f.label:
            # don't include first title of a page if that title has the
            # same text as the page.
            continue

        if f.key in ('title', 'subtitle', 'comment') and f.include_in_summary_page:
            entry = {'field': None}
            if f.key == 'title':
                finish_titles(current_page_fields)
                latest_title = entry
                has_contents_since_latest_title = False
                has_contents_since_latest_subtitle = False
            elif f.key == 'subtitle':
                finish_subtitles(current_page_fields)
                latest_subtitle = entry
                has_contents_since_latest_subtitle = False
            else:
                # comment is counted as content, as it's explicitely marked for summary page
                has_contents_since_latest_subtitle = True
                has_contents_since_latest_title = True

            entry['field'] = f
            current_page_fields.append(entry)
            continue

        if not hasattr(f, 'get_view_value'):
            continue

        if not (f.include_in_summary_page or wf_form):
            continue

        value, value_details = f.get_value_info(data, wf_form)
        if value is None and not (f.is_required() and include_unset_required_fields):
            continue

        if parent_field:
            value_details['parent_field'] = parent_field
            value_details['parent_field_index'] = parent_field_index

        current_page_fields.append({'field': f, 'value': value, 'value_details': value_details})
        has_contents_since_latest_subtitle = True
        has_contents_since_latest_title = True

    finish_titles(current_page_fields)

    if not pages:
        fields_and_details = current_page_fields
    else:
        # ignore empty pages
        fields_and_details = []
        for page in pages:
            if not any(bool('value' in x) for x in page['fields']):
                continue
            fields_and_details.append(page)
            fields_and_details.extend([x for x in page['fields']])

    return fields_and_details


def iter_summary_display_actions(
    filled, field_details, form_url='', include_unset_required_fields=False, wf_form=False
):
    from wcs.workflows import template_on_formdata

    on_page = None
    for field_value_info in field_details:
        f = field_value_info['field']
        parent_field = field_value_info.get('value_details', {}).get('parent_field')
        parent_field_index = field_value_info.get('value_details', {}).get('parent_field_index')
        if f.key == 'page':
            if on_page:
                yield {'action': 'close-page'}
            yield {'action': 'open-page', 'value': get_publisher().translate(f.label)}
            on_page = f
            continue

        if f.key == 'title':
            label = template_on_formdata(None, get_publisher().translate(f.label), autoescape=False)
            yield {'action': 'title', 'value': label, 'css': f.extra_css_class or ''}
            continue

        if f.key == 'subtitle':
            label = template_on_formdata(None, get_publisher().translate(f.label), autoescape=False)
            yield {'action': 'subtitle', 'value': label, 'css': f.extra_css_class or ''}
            continue

        if f.key == 'comment':
            yield {'action': 'comment', 'value': f.get_text(), 'css': f.extra_css_class or ''}
            continue

        css_classes = ['field', 'field-type-%s' % f.key]
        if f.extra_css_class:
            css_classes.append(f.extra_css_class)
        css_classes = ' '.join(css_classes)
        label_id = f'form-field-label-f{f.id}'
        if parent_field:
            label_id = f'form-field-label-f{parent_field.id}-r{parent_field_index}-s{f.id}'
        yield {'action': 'open-field', 'css': css_classes}
        if f.key == 'block' and f.label_display == 'subtitle':
            yield {
                'action': 'subtitle',
                'value': get_publisher().translate(f.label),
                'id': label_id,
                'css': '',
            }
        elif not (f.key == 'block' and f.label_display == 'hidden'):
            yield {
                'action': 'label',
                'value': get_publisher().translate(f.label),
                'id': label_id,
                'field': f,
            }
        value, value_details = field_value_info['value'], field_value_info['value_details']
        value_details['label_id'] = label_id
        value_details['formdata'] = filled
        if value is None:
            if not (f.key == 'block' and f.label_display == 'hidden'):
                yield {'action': 'value', 'value': None}
        elif f.key == 'block':
            block_field_details = f.get_value_details(
                formdata=filled,
                value=value_details.get('value_id'),
                include_unset_required_fields=include_unset_required_fields,
                wf_form=wf_form,
            )
            yield {'action': 'open-block-value'}
            yield from iter_summary_display_actions(
                filled,
                block_field_details,
                form_url=form_url,
                include_unset_required_fields=include_unset_required_fields,
                wf_form=wf_form,
            )
            yield {'action': 'close-block-value'}
        else:
            s = f.get_view_value(
                value,
                summary=True,
                include_unset_required_fields=include_unset_required_fields,
                **value_details,
            )
            s = s.replace('[download]', str('%sdownload' % form_url))
            yield {'action': 'value', 'value': s, 'field_value_info': field_value_info}
        yield {'action': 'close-field'}

    if on_page:
        yield {'action': 'close-page'}


def get_summary_display_actions(
    filled, fields=None, form_url='', include_unset_required_fields=False, wf_form=False
):
    field_details = get_summary_field_details(
        filled,
        fields,
        include_unset_required_fields=include_unset_required_fields,
        wf_form=wf_form,
    )
    yield from iter_summary_display_actions(
        filled,
        field_details,
        form_url=form_url,
        include_unset_required_fields=include_unset_required_fields,
        wf_form=wf_form,
    )


def display_fields(filled, fields=None, form_url='', include_unset_required_fields=False, wf_form=False):
    r = TemplateIO(html=True)

    for field_action in get_summary_display_actions(
        filled,
        fields,
        include_unset_required_fields=include_unset_required_fields,
        wf_form=wf_form,
    ):
        if field_action['action'] == 'close-page':
            r += htmltext('</div>')
            r += htmltext('</div>')
        elif field_action['action'] == 'open-page':
            r += htmltext('<div class="page">')
            r += htmltext('<h3>%s</h3>') % field_action['value']
            r += htmltext('<div>')
        elif field_action['action'] == 'open-block-value':
            r += htmltext('<div class="value value--block">')
        elif field_action['action'] == 'close-block-value':
            r += htmltext('</div>')
        elif field_action['action'] == 'title':
            r += htmltext('<div class="title %s"><h3>%s</h3></div>') % (
                field_action['css'],
                field_action['value'],
            )
        elif field_action['action'] == 'subtitle':
            label_id = field_action.get('id')
            if label_id:
                r += htmltext('<div class="subtitle %s" id="%s"><h4>%s</h4></div>') % (
                    field_action['css'],
                    label_id,
                    field_action['value'],
                )
            else:
                r += htmltext('<div class="subtitle %s"><h4>%s</h4></div>') % (
                    field_action['css'],
                    field_action['value'],
                )
        elif field_action['action'] == 'comment':
            r += htmltext(
                '<div class="comment-field %s">%s</div>' % (field_action['css'], field_action['value'])
            )
        elif field_action['action'] == 'open-field':
            r += htmltext('<div class="%s">' % field_action['css'])
        elif field_action['action'] == 'close-field':
            r += htmltext('</div>')
        elif field_action['action'] == 'label':
            r += htmltext('<p id="%s" class="label">%s</p> ') % (
                field_action['id'],
                field_action['value'],
            )
        elif field_action['action'] == 'value':
            value = field_action['value']
            if value is None:
                r += htmltext('<div class="value"><i>%s</i></div>') % _('Not set')
            else:
                if isinstance(value, htmltext) and str(value).startswith(
                    ('<div', '<p', '<table', '<ul', '<ol')
                ):
                    r += htmltext('<div class="value">')
                    r += value
                    r += htmltext('</div>')
                else:
                    r += htmltext('<p class="value">')
                    r += value
                    r += htmltext('</p>')

        r += '\n'

    return r.getvalue()
