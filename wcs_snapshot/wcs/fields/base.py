# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

import collections
import copy
import html
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

from django.utils.encoding import force_str
from quixote import get_publisher, get_request
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, get_cfg, misc
from wcs.qommon.form import (
    CheckboxesWidget,
    CheckboxWidget,
    CompositeWidget,
    ConditionWidget,
    Form,
    MiniRichTextWidget,
    OptGroup,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    TextWidget,
    VarnameWidget,
)
from wcs.qommon.misc import ellipsize, get_dependencies_from_template, xml_node_text
from wcs.qommon.template import Template, TemplateError


class SetValueError(Exception):
    pass


class PrefillSelectionWidget(CompositeWidget):
    def __init__(self, name, value=None, field=None, use_textarea=False, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)

        if not value:
            value = {}

        options = [
            ('none', _('None')),
            ('string', _('String / Template')),
        ]
        options += [
            ('user', _('User Field')),
            ('geolocation', _('Geolocation')),
        ]

        if field and field.key == 'items':
            # limit choices strings (must be templates giving complex data);
            # items field are prefilled with list of strings
            options = [x for x in options if x[0] in ('none', 'string')]
        elif field and field.key == 'map':
            # limit choices to geolocation
            options = [x for x in options if x[0] in ('none', 'string', 'geolocation')]

        self.add(
            SingleSelectWidget,
            'type',
            options=options,
            value=value.get('type') or 'none',
            attrs={'data-dynamic-display-parent': 'true'},
        )

        self.parse()
        if not self.value or self.value.get('type') == 'none':
            self.value = {}

        self.prefill_types = prefill_types = collections.OrderedDict(options)
        self.add(
            StringWidget if use_textarea is False else TextWidget,
            'value_string',
            value=value.get('value') if value.get('type') == 'string' else None,
            attrs={
                'data-dynamic-display-child-of': 'prefill$type',
                'data-dynamic-display-value': prefill_types.get('string'),
            },
        )

        formdef = get_publisher().user_class.get_formdef()
        users_cfg = get_cfg('users', {})
        if formdef:
            user_fields = []
            for user_field in formdef.fields:
                if user_field.label in [x[1] for x in user_fields]:
                    # do not allow duplicated field names
                    continue
                user_fields.append((user_field.id, user_field.label))
            if not users_cfg.get('field_email'):
                user_fields.append(('email', _('Email (builtin)')))
        else:
            user_fields = [('name', _('Name')), ('email', _('Email'))]
        self.add(
            SingleSelectWidget,
            'value_user',
            value=value.get('value') if value.get('type') == 'user' else None,
            options=user_fields,
            attrs={
                'data-dynamic-display-child-of': 'prefill$type',
                'data-dynamic-display-value': prefill_types.get('user'),
            },
        )

        if field and field.key == 'map':
            # different prefilling sources on map fields
            geoloc_fields = [
                ('position', _('Device geolocation')),
                ('position-front-only', _('Device geolocation (only in frontoffice)')),
            ]
        else:
            geoloc_fields = [
                ('house', _('Number')),
                ('road', _('Street')),
                ('number-and-street', _('Number and street')),
                ('postcode', _('Post Code')),
                ('city', _('City')),
                ('country', _('Country')),
            ]
            if field and field.key == 'item':
                geoloc_fields.append(('address-id', _('Address Identifier')))
        self.add(
            SingleSelectWidget,
            'value_geolocation',
            value=value.get('value') if value.get('type') == 'geolocation' else None,
            options=geoloc_fields,
            attrs={
                'data-dynamic-display-child-of': 'prefill$type',
                'data-dynamic-display-value': prefill_types.get('geolocation'),
            },
        )

        # exclude geolocation from locked prefill as the data necessarily
        # comes from the user device.
        self.add(
            CheckboxWidget,
            'locked',
            value=value.get('locked'),
            attrs={
                'data-dynamic-display-child-of': 'prefill$type',
                'data-dynamic-display-value-in': '|'.join(
                    [str(x[1]) for x in options if x[0] not in ('none', 'geolocation')]
                ),
                'inline_title': _('Locked'),
            },
        )
        self.add(
            CheckboxWidget,
            'locked-unless-empty',
            value=value.get('locked-unless-empty'),
            attrs={
                'data-dynamic-display-child-of': 'prefill$type',
                'data-dynamic-display-value-in': '|'.join(
                    [str(x[1]) for x in options if x[0] not in ('none', 'user', 'geolocation')]
                ),
                'inline_title': _('Unless empty'),
            },
        )

        self.initial_value = value
        self._parsed = False

    def _parse(self, request):
        values = {}
        type_ = self.get('type')
        if type_ and type_ != 'none':
            values['type'] = type_
            values['locked'] = self.get('locked')
            values['locked-unless-empty'] = self.get('locked-unless-empty')
            value = self.get('value_%s' % type_)
            if value:
                values['value'] = value
        self.value = values or None
        if values and values['type'] == 'string' and Template.is_template_string(values.get('value')):
            try:
                Template(values.get('value'), raises=True)
            except TemplateError as e:
                self.set_error(str(e))

    def render_content(self):
        r = TemplateIO(html=True)
        for widget in self.get_widgets():
            r += widget.render_content()
        return r.getvalue()


class Field:
    id = None
    varname = None
    label = None
    extra_css_class = None
    convert_value_from_str = None
    convert_value_to_str = None
    convert_value_from_anything = None
    allow_complex = False
    allow_statistics = False
    display_locations = []
    prefill = None
    keep_raw_value = True
    store_display_value = None
    store_structured_value = None
    get_opendocument_node_value = None
    condition = None
    documentation = None

    section = 'data'
    is_no_data_field = False
    can_include_in_listing = False
    available_for_filter = False

    # flag a field for removal by AnonymiseWorkflowStatusItem
    # possible values are final, intermediate, no.
    # can be overriden in field' settings
    anonymise = 'final'
    stats = None

    # declarations for serialization, they are mostly for legacy files,
    # new exports directly include typing attributes.
    TEXT_ATTRIBUTES = ['label', 'type', 'hint', 'varname', 'extra_css_class', 'documentation']

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k.replace('-', '_'), v)

    def __getstate__(self):
        odict = copy.copy(self.__dict__)
        odict.pop('_formdef', None)
        return odict

    @classmethod
    def init(cls):
        pass

    def get_type_label(self):
        return self.description

    def get_admin_url(self):
        if not getattr(self, '_formdef', None):
            return ''
        return self._formdef.get_field_admin_url(field=self)

    def get_admin_url_label(self):
        return _('%(form)s, field: "%(field)s" (%(type)s)') % {
            'form': self._formdef.name,
            'field': self.ellipsized_label,
            'type': self.get_type_label(),
        }

    @property
    def include_in_listing(self):
        return 'listings' in (self.display_locations or [])

    @property
    def include_in_validation_page(self):
        return 'validation' in (self.display_locations or [])

    @property
    def include_in_summary_page(self):
        return 'summary' in (self.display_locations or [])

    @property
    def include_in_statistics(self):
        return self.allow_statistics and self.varname and 'statistics' in (self.display_locations or [])

    @property
    def unhtmled_label(self):
        return force_str(html.unescape(force_str(re.sub('<.*?>', ' ', self.label or ''))).strip())

    @property
    def ellipsized_label(self):
        return ellipsize(self.unhtmled_label)

    def get_admin_attributes(self):
        return ['label', 'condition']

    def get_display_locations_options(self):
        options = [
            ('validation', _('Validation Page')),
            ('summary', _('Summary Page')),
        ]
        if self.can_include_in_listing:
            options.append(('listings', _('Management Listings')))

        if self.allow_statistics:
            options.append(('statistics', _('Statistics')))

        return options

    def export_to_json(self, include_id=False):
        field = {}
        if include_id:
            extra_fields = ['id']
        else:
            extra_fields = []
        for attribute in self.get_admin_attributes() + extra_fields:
            if attribute == 'display_locations':
                continue
            if hasattr(self, attribute) and getattr(self, attribute) is not None:
                val = getattr(self, attribute)
                field[attribute] = val
        field['type'] = self.key
        field['in_statistics'] = self.include_in_statistics
        return field

    def init_with_json(self, elem, include_id=False):
        if include_id:
            self.id = elem.get('id')
        for attribute in self.get_admin_attributes():
            if attribute in elem:
                setattr(self, attribute, elem.get(attribute))

    def export_to_xml(self, include_id=False):
        field = ET.Element('field')
        extra_fields = ['default_value', 'documentation']  # default_value is specific to workflow variables
        if include_id:
            extra_fields.append('id')
        ET.SubElement(field, 'type').text = self.key
        for attribute in self.get_admin_attributes() + extra_fields:
            if hasattr(self, '%s_export_to_xml' % attribute):
                getattr(self, '%s_export_to_xml' % attribute)(field, include_id=include_id)
                continue
            if hasattr(self, attribute) and getattr(self, attribute) is not None:
                val = getattr(self, attribute)
                if isinstance(val, dict) and not val:
                    continue
                el = ET.SubElement(field, attribute)
                if isinstance(val, dict):
                    for k, v in sorted(val.items()):
                        # field having non str value in dictionnary field must overload
                        # import_to_xml to handle import
                        ET.SubElement(el, k).text = force_str(v)
                elif isinstance(val, list):
                    if attribute[-1] == 's':
                        atname = attribute[:-1]
                    else:
                        atname = 'item'
                    # noqa pylint: disable=not-an-iterable
                    for v in val:
                        ET.SubElement(el, atname).text = force_str(v)
                else:
                    if isinstance(val, bool):
                        el.attrib['type'] = 'bool'
                    elif isinstance(val, int):
                        el.attrib['type'] = 'int'
                    elif isinstance(val, Decimal):
                        el.attrib['type'] = 'decimal'
                        val = misc.parse_decimal(val)
                    elif isinstance(val, str):
                        el.attrib['type'] = 'str'
                    el.text = str(val)
        return field

    def init_with_xml(self, elem, include_id=False, snapshot=False):
        extra_fields = ['documentation', 'default_value']  # default_value is specific to workflow variables
        for attribute in self.get_admin_attributes() + extra_fields:
            el = elem.find(attribute)
            if hasattr(self, '%s_init_with_xml' % attribute):
                getattr(self, '%s_init_with_xml' % attribute)(el, include_id=include_id, snapshot=False)
                continue
            if el is None:
                continue
            if list(el):
                if isinstance(getattr(self, attribute), list):
                    v = [xml_node_text(x) for x in el]
                elif isinstance(getattr(self, attribute), dict):
                    v = {}
                    for e in el:
                        v[e.tag] = xml_node_text(e)
                else:
                    print('currently:', self.__dict__)
                    print('  attribute:', attribute)
                    # ???
                    raise AssertionError
                setattr(self, attribute, v)
            else:
                if attribute in self.TEXT_ATTRIBUTES:
                    elem_type = 'str'
                else:
                    elem_type = el.attrib.get('type')
                if el.text is None:
                    if isinstance(getattr(self, attribute), list):
                        setattr(self, attribute, [])
                    else:
                        setattr(self, attribute, None)
                elif elem_type == 'bool' or (not elem_type and el.text in ('False', 'True')):
                    # boolean
                    setattr(self, attribute, el.text == 'True')
                elif elem_type == 'int' or (not elem_type and isinstance(getattr(self, attribute), int)):
                    setattr(self, attribute, int(el.text))
                elif elem_type == 'decimal' or (
                    not elem_type and isinstance(getattr(self, attribute), Decimal)
                ):
                    setattr(self, attribute, misc.parse_decimal(el.text))
                else:
                    setattr(self, attribute, xml_node_text(el))
        if include_id:
            try:
                self.id = xml_node_text(elem.find('id'))
            except Exception:
                pass

    def condition_init_with_xml(self, node, include_id=False, snapshot=False):
        self.condition = None
        if node is None:
            return
        self.condition = {
            'type': xml_node_text(node.find('type')),
            'value': xml_node_text(node.find('value')),
        }

    def data_source_init_with_xml(self, node, include_id=False, snapshot=False):
        self.data_source = {}
        if node is None:
            return
        if node.findall('type'):
            self.data_source = {
                'type': xml_node_text(node.find('type')),
                'value': xml_node_text(node.find('value')),
            }
            if self.data_source.get('type') is None:
                self.data_source = {}
            elif self.data_source.get('value') is None:
                del self.data_source['value']

    def prefill_init_with_xml(self, node, include_id=False, snapshot=False):
        self.prefill = {}
        if node is not None and node.findall('type'):
            self.prefill = {
                'type': xml_node_text(node.find('type')),
            }
            if self.prefill['type'] and self.prefill['type'] != 'none':
                self.prefill['value'] = xml_node_text(node.find('value'))
                if xml_node_text(node.find('locked')) == 'True':
                    self.prefill['locked'] = True
                if xml_node_text(node.find('locked-unless-empty')) == 'True':
                    self.prefill['locked-unless-empty'] = True

    def display_locations_export_to_xml(self, node, include_id=False):
        display_locations_node = ET.SubElement(node, 'display_locations')
        for v in self.display_locations or []:
            ET.SubElement(display_locations_node, 'item').text = force_str(v)

    def get_rst_view_value(self, value, indent=''):
        return indent + self.get_view_value(value)

    def get_csv_heading(self):
        return []

    def get_csv_value(self, element, **kwargs):
        return []

    def get_structured_value(self, data):
        if not self.store_structured_value:
            return None
        return data.get('%s_structured' % self.id)

    def get_prefill_configuration(self):
        if self.prefill and self.prefill.get('type') == 'none':
            # make sure a 'none' prefill is not considered as a value
            self.prefill = None
        return self.prefill or {}

    def get_prefill_value(self, user=None, force_string=True):
        # returns a tuple with two items,
        #  1. value[str], the value that will be used to prefill
        #  2. locked[bool], a flag to know if this is a locked value
        #     (because it has been explicitely marked so or because it
        #     comes from verified identity data).
        t = self.prefill.get('type')

        explicit_lock = bool(self.prefill.get('locked'))
        explicit_lock_unless_empty = bool(self.prefill.get('locked-unless-empty'))
        prefill_value = None

        if t == 'string':
            value = self.prefill.get('value')
            if not Template.is_template_string(value):
                return (value, explicit_lock)

            from wcs.workflows import WorkflowStatusItem

            try:
                with get_publisher().complex_data():
                    v = WorkflowStatusItem.compute(
                        value,
                        raises=True,
                        allow_complex=self.allow_complex and not force_string,
                        record_errors=False,
                    )
                    if v and self.allow_complex:
                        v = get_publisher().get_cached_complex_data(v)
                prefill_value = v
            except TemplateError as e:
                get_publisher().record_error(
                    _('Failed to evaluate prefill on field "%s"') % self.label,
                    exception=e,
                )
                prefill_value = ''

        elif t == 'user' and not user:
            explicit_lock_unless_empty = True

        elif t == 'user' and user:
            x = self.prefill.get('value')
            if x == 'phone':
                # get mapped field
                x = get_cfg('users', {}).get('field_phone') or x
            if x == 'email':
                if 'email' in (user.verified_fields or []):
                    # force lock for verified fields
                    explicit_lock = True
                prefill_value = user.email
            elif user.form_data:
                userform = user.get_formdef()
                for userfield in userform.fields:
                    if userfield.id == x:
                        value = user.form_data.get(x)
                        if (
                            value
                            and getattr(userfield, 'validation', None)
                            and userfield.validation['type'] in ('phone', 'phone-fr')
                        ):
                            country_code = None
                            if (
                                getattr(self, 'validation', None)
                                and self.validation.get('type') == 'phone-fr'
                            ):
                                country_code = 'FR'
                            value = misc.get_formatted_phone(user.form_data.get(x), country_code)

                        if str(userfield.id) in (user.verified_fields or []):
                            # force lock for verified fields
                            explicit_lock = True
                        prefill_value = value
                        break

        elif t == 'geolocation':
            prefill_value = None
            explicit_lock = False

        if explicit_lock and explicit_lock_unless_empty and not bool(prefill_value):
            explicit_lock = False

        return (prefill_value, explicit_lock)

    def get_prefill_attributes(self):
        if not self.get_prefill_configuration():
            return
        t = self.prefill.get('type')

        if t == 'geolocation':
            value = self.prefill.get('value')
            if value == 'position-front-only':
                if get_request() and get_request().is_in_backoffice():
                    return {}
                value = 'position'
            return {'geolocation': value}

        if t == 'user':
            formdef = get_publisher().user_class.get_formdef()
            for user_field in formdef.fields or []:
                if user_field.id != self.prefill.get('value'):
                    continue
                try:
                    autocomplete_attribute = re.search(
                        r'\bautocomplete-([a-z0-9-]+)', user_field.extra_css_class
                    ).groups()[0]
                except (TypeError, IndexError, AttributeError):
                    continue
                return {'autocomplete': autocomplete_attribute}

        return None

    def feed_session(self, value, display_value):
        pass

    def migrate(self):
        changed = False
        if getattr(self, 'in_listing', None):  # 2019-09-28
            self.display_locations = self.display_locations[:]
            self.display_locations.append('listings')
            changed = True
            self.in_listing = None
        if isinstance(self.anonymise, bool):  # 2023-06-13
            self.anonymise = 'final' if self.anonymise else 'no'
            changed = True
        if isinstance(getattr(self, 'required', None), bool):  # 2025-03-02
            self.required = 'required' if getattr(self, 'required', None) is True else 'optional'
            changed = True
        return changed

    @staticmethod
    def evaluate_condition(
        dict_vars, formdef, condition, source_label=None, source_url=None, record_errors=True
    ):
        from .page import PageCondition

        return PageCondition(condition, {'dict_vars': dict_vars, 'formdef': formdef}, record_errors).evaluate(
            source_label=source_label, source_url=source_url
        )

    def is_visible(self, dict, formdef):
        try:
            return self.evaluate_condition(
                dict,
                formdef,
                self.condition,
                source_label=_('Field: %s') % self.ellipsized_label,
                source_url=self.get_admin_url(),
            )
        except RuntimeError:
            return True

    @classmethod
    def get_referenced_varnames(cls, formdef, value):
        return re.findall(
            r'\b(?:%s)[_\.]var[_\.]([a-zA-Z0-9_]+?)(?:_raw|_live_|_structured_|_var_|\b)'
            % '|'.join(formdef.var_prefixes),
            str(value or ''),
        )

    def get_condition_varnames(self, formdef):
        return self.get_referenced_varnames(formdef, self.condition['value'])

    def has_live_conditions(self, formdef, hidden_varnames=None):
        varnames = self.get_condition_varnames(formdef)
        if not varnames:
            return False
        field_position = formdef.fields.index(self)
        # rewind to field page
        for field_position in range(field_position, -1, -1):
            if formdef.fields[field_position].key == 'page':
                break
        else:
            field_position = -1  # form with no page
        # start from there
        for field in formdef.fields[field_position + 1 :]:
            if field.key == 'page':
                # stop at next page
                break
            if field.varname in varnames and (
                hidden_varnames is None or field.varname not in hidden_varnames
            ):
                return True
        return False

    def from_json_value(self, value):
        if value is None:
            return value
        return str(value)

    def set_value(self, data, value, raise_on_error=False):
        data['%s' % self.id] = value
        if self.store_display_value:
            display_value = self.store_display_value(data, self.id)
            if raise_on_error and display_value is None:
                raise SetValueError(_('datasource is unavailable (field id: %s)') % self.id)
            data['%s_display' % self.id] = display_value or None
        if self.store_structured_value and value:
            structured_value = self.store_structured_value(data, self.id, raise_on_error=raise_on_error)
            if structured_value:
                if isinstance(structured_value, dict) and structured_value.get('id'):
                    # in case of list field, override id
                    data['%s' % self.id] = str(structured_value.get('id'))
                data['%s_structured' % self.id] = structured_value
            else:
                data['%s_structured' % self.id] = None
        elif self.store_structured_value:
            data['%s_structured' % self.id] = None

    def get_dependencies(self):
        if getattr(self, 'data_source', None):
            data_source_type = self.data_source.get('type')
            if data_source_type and data_source_type.startswith('carddef:'):
                from wcs.carddef import CardDef

                carddef_slug = data_source_type.split(':')[1]
                try:
                    yield CardDef.get_by_urlname(carddef_slug)
                except KeyError:
                    pass
            else:
                from wcs.data_sources import NamedDataSource

                yield NamedDataSource.get_by_slug(data_source_type, ignore_errors=True)
        if getattr(self, 'prefill', None):
            prefill = self.prefill
            if prefill:
                if prefill.get('type') == 'string':
                    yield from get_dependencies_from_template(prefill.get('value'))
        if getattr(self, 'condition', None):
            condition = self.condition
            if condition:
                if condition.get('type') == 'django':
                    yield from get_dependencies_from_template(condition.get('value'))

    def get_parameters_view(self):
        r = TemplateIO(html=True)
        form = Form()
        self.fill_admin_form(form, formdef=None)
        parameters = [x for x in self.get_admin_attributes() if getattr(self, x, None) is not None]
        r += htmltext('<ul>')
        for parameter in parameters:
            widget = form.get_widget(parameter)
            if not widget:
                continue
            label = self.get_parameter_view_label(widget, parameter)
            if not label:
                continue
            value = getattr(self, parameter, Ellipsis)
            if value is None or value == getattr(self.__class__, parameter, Ellipsis):
                continue
            parameter_view_value = self.get_parameter_view_value(widget, parameter)
            if parameter_view_value:
                r += htmltext('<li class="parameter-%s">' % parameter)
                r += htmltext('<span class="parameter">%s</span> ') % _('%s:') % label
                r += parameter_view_value
                r += htmltext('</li>')
        r += htmltext('</ul>')
        return r.getvalue()

    def get_parameter_view_label(self, widget, parameter):
        if hasattr(self, 'get_%s_parameter_view_label' % parameter):
            return getattr(self, 'get_%s_parameter_view_label' % parameter)()
        return widget.get_title()

    def get_parameter_view_value(self, widget, parameter):
        if hasattr(self, 'get_%s_parameter_view_value' % parameter):
            return getattr(self, 'get_%s_parameter_view_value' % parameter)(widget)
        value = getattr(self, parameter)
        if isinstance(value, bool):
            return str(_('Yes') if value else _('No'))
        if hasattr(widget, 'options') and value:
            if not isinstance(widget, CheckboxesWidget):
                value = [value]
            value_labels = []
            for option in widget.options:
                if isinstance(option, tuple):
                    if option[0] in value:
                        value_labels.append(str(option[1]))
                else:
                    if option in value:
                        value_labels.append(str(option))
            return ', '.join(value_labels) if value_labels else '-'
        if isinstance(value, list):
            return ', '.join(value)

        return str(value)

    def get_prefill_parameter_view_value(self, widget):
        value = self.get_prefill_configuration()
        if not value:
            return
        r = TemplateIO(html=True)
        r += htmltext('<ul>')
        r += htmltext('<li><span class="parameter">%s%s</span> %s</li>') % (
            _('Type'),
            _(':'),
            widget.prefill_types.get(value.get('type')),
        )
        if value.get('type') in ('user', 'geolocation'):
            select_widget = widget.get_widget('value_%s' % value['type'])
            labels = {x[0]: x[1] for x in select_widget.options}
            r += htmltext('<li><span class="parameter">%s%s</span> %s</li>') % (
                _('Value'),
                _(':'),
                labels.get(value.get('value'), '-'),
            )
        else:
            r += htmltext('<li><span class="parameter">%s%s</span> %s</li>') % (
                _('Value'),
                _(':'),
                value.get('value'),
            )
        if value.get('locked'):
            r += htmltext('<li>%s</li>') % _('Locked')
        r += htmltext('</ul>')
        return r.getvalue()

    def get_data_source_parameter_view_value(self, widget):
        value = getattr(self, 'data_source', None)
        if not value or value.get('type') == 'none':
            return

        if value.get('type').startswith('carddef:'):
            from wcs.carddef import CardDef

            parts = value['type'].split(':')
            try:
                carddef = CardDef.get_by_urlname(parts[1])
            except KeyError:
                return str(_('deleted card model'))
            custom_view = CardDef.get_data_source_custom_view(value['type'], carddef=carddef)
            r = htmltext('<a href="%(url)s">%(label)s</a>') % {
                'label': _('card model: %s') % carddef.name,
                'url': carddef.get_admin_url(),
            }
            if custom_view:
                r += ', '
                r += htmltext('<a href="%(url)s">%(label)s</a>') % {
                    'label': _('custom view: %s') % custom_view.title,
                    'url': '%s%s' % (carddef.get_url(), custom_view.get_url_slug()),
                }
            return r

        data_source_types = {
            'json': _('JSON URL'),
            'jsonp': _('JSONP URL'),
            'geojson': _('GeoJSON URL'),
            'jsonvalue': _('JSON Expression'),
        }
        if value.get('type') in data_source_types:
            return '%s - %s' % (data_source_types[value.get('type')], value.get('value'))

        from wcs.data_sources import NamedDataSource

        data_source = NamedDataSource.get_by_slug(value['type'], stub_fallback=True)
        return htmltext('<a href="%(url)s">%(label)s</a>') % {
            'label': data_source.name,
            'url': data_source.get_admin_url(),
        }

    def get_condition_parameter_view_value(self, widget):
        if not self.condition or self.condition.get('type') == 'none':
            return
        return htmltext('<tt class="condition">%s</tt> <span class="condition-type">(%s)</span>') % (
            self.condition['value'],
            {'django': 'Django'}.get(self.condition['type']),
        )

    def __repr__(self):
        return '<%s %s %r>' % (self.__class__.__name__, self.id, self.label and self.label[:64])

    def __hash__(self):
        return hash((self.id, self.key))

    def __eq__(self, other):
        return self.__class__ is other.__class__ and self.id == other.id

    def i18n_scan(self, base_location):
        location = '%s%s/' % (base_location, self.id)
        yield location, None, self.label
        yield location, None, getattr(self, 'hint', None)


class WidgetField(Field):
    hint = None
    required = 'required'
    display_locations = ['validation', 'summary']
    extra_attributes = []
    prefill = {}
    prefill_selection_widget_kwargs = {}

    widget_class = None
    use_live_server_validation = False
    can_include_in_listing = True

    def is_required(self):
        return bool(self.required == 'required') or (
            self.required == 'frontoffice' and get_request() and get_request().is_in_frontoffice()
        )

    def add_to_form(self, form, value=None):
        kwargs = {'required': self.is_required(), 'render_br': False}
        if value:
            kwargs['value'] = value
        for k in self.extra_attributes:
            if hasattr(self, k):
                kwargs[k] = getattr(self, k)
        with get_publisher().error_context(
            source_label=_('Field: %s') % self.ellipsized_label, source_url=self.get_admin_url()
        ):
            self.perform_more_widget_changes(form, kwargs)
        if self.hint and self.hint.startswith('<'):
            hint = htmltext(get_publisher().translate(self.hint))
        else:
            hint = get_publisher().translate(self.hint or '')
        form.add(self.widget_class, 'f%s' % self.id, title=self.label, hint=hint, **kwargs)
        widget = form.get_widget('f%s' % self.id)
        widget.field = self
        widget.use_live_server_validation = self.use_live_server_validation
        if self.extra_css_class:
            if hasattr(widget, 'extra_css_class') and widget.extra_css_class:
                widget.extra_css_class = '%s %s' % (widget.extra_css_class, self.extra_css_class)
            else:
                widget.extra_css_class = self.extra_css_class
        if self.varname:
            widget.div_id = 'var_%s' % self.varname
        if (
            getattr(get_request(), 'backoffice_form_preview', None)
            and len(getattr(widget, 'options', None) or []) >= 100
        ):
            widget.display_too_many_choices_warning = True
        return widget

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        pass

    def add_to_view_form(self, form, value=None):
        kwargs = {'render_br': False}

        self.field_key = 'f%s' % self.id
        self.perform_more_widget_changes(form, kwargs, False)

        for k in self.extra_attributes:
            if hasattr(self, k):
                kwargs[k] = getattr(self, k)

        if self.widget_class is StringWidget and 'size' not in kwargs and value:
            # set a size if there is not one already defined, this will be for
            # example the case with ItemField
            kwargs['size'] = len(value)

        form.add(
            self.widget_class, self.field_key, title=self.label, value=value, readonly='readonly', **kwargs
        )
        widget = form.get_widget(self.field_key)
        widget.transfer_form_value(get_request())
        widget.field = self
        if self.extra_css_class:
            if hasattr(widget, 'extra_css_class') and widget.extra_css_class:
                widget.extra_css_class = '%s %s' % (widget.extra_css_class, self.extra_css_class)
            else:
                widget.extra_css_class = self.extra_css_class
        return widget

    def get_anonymise_options(self):
        if get_publisher().has_site_option('enable-intermediate-anonymisation'):
            return [
                ('final', _('Data deleted on final anonymisation'), 'final'),
                (
                    'intermediate',
                    _('Data deleted on both intermediate and final anonymisation'),
                    'intermediate',
                ),
                ('no', _('Data kept after anonymisation'), 'no'),
            ]

        return [('final', _('Yes'), 'final'), ('no', _('No'), 'no')]

    def fill_admin_form(self, form, formdef):
        form.add(StringWidget, 'label', title=_('Label'), value=self.label, required=True, size=50)
        required_options = [
            ('required', _('Yes'), 'required'),
            ('optional', _('No'), 'optional'),
        ]
        if formdef is None or formdef.may_appear_in_frontoffice:
            required_options.append(('frontoffice', _('Only in frontoffice'), 'frontoffice'))
        form.add(
            RadiobuttonsWidget,
            'required',
            title=_('Required'),
            options=required_options,
            value=self.required,
            default_value=self.__class__.required,
            extra_css_class='widget-inline-radio',
        )
        form.add(MiniRichTextWidget, 'hint', title=_('Hint'), value=self.hint, cols=60, rows=3)
        form.add(
            VarnameWidget,
            'varname',
            title=_('Identifier'),
            value=self.varname,
            size=30,
            advanced=False,
            hint=_('This is used as suffix for variable names.'),
        )
        form.add(
            CheckboxesWidget,
            'display_locations',
            title=_('Display Locations'),
            options=self.get_display_locations_options(),
            value=self.display_locations,
            tab=('display', _('Display')),
            default_value=self.__class__.display_locations,
        )
        form.add(
            CssClassesWidget,
            'extra_css_class',
            title=_('Extra classes for CSS styling'),
            value=self.extra_css_class,
            size=30,
            tab=('display', _('Display')),
        )
        form.add(
            PrefillSelectionWidget,
            'prefill',
            title=_('Prefill'),
            value=self.prefill,
            advanced=True,
            field=self,
            **self.prefill_selection_widget_kwargs,
        )
        form.add(
            ConditionWidget,
            'condition',
            title=_('Display Condition'),
            value=self.condition,
            required=False,
            size=50,
            tab=('display', _('Display')),
        )
        # let override anonymise flag default value
        form.add(
            RadiobuttonsWidget,
            'anonymise',
            title=_('Anonymisation'),
            options=self.get_anonymise_options(),
            value=self.anonymise,
            advanced=True,
            hint=_('Marks the field data for removal in the anonymisation processes.'),
            default_value=self.__class__.anonymise,
        )

    def check_admin_form(self, form):
        display_locations = form.get_widget('display_locations').parse() or []
        varname = form.get_widget('varname').parse()
        if 'statistics' in display_locations and not varname:
            form.set_error(
                'display_locations', _('Field must have a varname in order to be displayed in statistics.')
            )

    def get_admin_attributes(self):
        return Field.get_admin_attributes(self) + [
            'required',
            'hint',
            'varname',
            'display_locations',
            'extra_css_class',
            'prefill',
            'anonymise',
        ]

    def get_csv_heading(self):
        return [self.label]

    def get_value_info(self, data, wf_form=False):
        # return the selected value and an optional dictionary that will be
        # passed to get_view_value() to provide additional details.
        value_details = {}
        if self.id not in data:
            value = None
        else:
            if self.store_display_value and ('%s_display' % self.id) in data:
                value = data['%s_display' % self.id]
                value_details['value_id'] = data[self.id]
            else:
                value = data[self.id]

            if value is None or value == '':
                value = None
        return (value, value_details)

    def get_view_value(self, value, **kwargs):
        return str(value) if value else ''

    def get_view_short_value(self, value, max_len=30, **kwargs):
        return self.get_view_value(value)

    def get_csv_value(self, element, **kwargs):
        if self.convert_value_to_str:
            return [self.convert_value_to_str(element)]
        return [element]

    def get_fts_value(self, data, **kwargs):
        if self.store_display_value:
            return data.get('%s_display' % self.id)
        return data.get(str(self.id))


field_classes = []
field_types = []


def register_field_class(klass):
    if klass not in field_classes:
        field_classes.append(klass)
        field_types.append((klass.key, klass.description))
        klass.init()


def get_field_class_by_type(type):
    from wcs.blocks import BlockDef

    from .block import BlockField

    for k in field_classes:
        if k.key == type:
            return k
    if type.startswith('block:'):
        # make sure block type exists (raises KeyError on missing data)
        BlockDef.get_on_index(type[6:], 'slug')
        return BlockField
    raise KeyError()


class CssClassesWidget(StringWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validation_function = self.validate_css_classes

    @classmethod
    def validate_css_classes(cls, value):
        if not re.match(r'^(\s*[a-zA-Z_][\w_-]+\s*)+$', value):
            raise ValueError(_('The value must consist of one or several valid names.'))


def get_field_options(blacklisted_types):
    from wcs.blocks import BlockDef

    disabled_fields = (get_publisher().get_site_option('disabled-fields') or '').split(',')
    disabled_fields = [f.strip() for f in disabled_fields if f.strip()]

    group_labels = {
        'data': _('Data'),
        'display': _('Display'),
        'blocks': _('Blocks of fields'),
        'agendas': _('Agendas'),
    }

    grouped_fields = collections.defaultdict(list)

    for klass in field_classes:
        if klass.key in blacklisted_types:
            continue
        if klass.key in disabled_fields:
            continue
        grouped_fields[klass.section].append((klass.key, klass.description, klass.key))

    if not blacklisted_types or 'blocks' not in blacklisted_types:
        for blockdef in BlockDef.select(order_by='name'):
            grouped_fields['blocks'].append(
                ('block:%s' % blockdef.slug, blockdef.name, 'block:%s' % blockdef.slug)
            )

    options = []
    for group_key, group_fields in grouped_fields.items():
        if not group_fields:
            continue
        if group_key == 'display':
            group_fields.sort(key=lambda x: ['page', 'title', 'subtitle', 'comment'].index(x[0]))
        else:
            group_fields.sort(key=lambda x: x[1])
        options.extend([OptGroup(group_labels[group_key])] + group_fields)

    return options
