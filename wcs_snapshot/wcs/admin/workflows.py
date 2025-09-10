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
import itertools
import json
import textwrap
import xml.etree.ElementTree as ET
from subprocess import PIPE, Popen

from django.utils.encoding import force_bytes
from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.categories import WorkflowCategoriesDirectory, get_categories
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.deprecations import DeprecationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.carddef import CardDef
from wcs.categories import WorkflowCategory
from wcs.formdata import Evolution
from wcs.formdef import FormDef
from wcs.qommon import _, errors, force_str, misc, template
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import (
    CheckboxWidget,
    ColourWidget,
    ComputedExpressionWidget,
    FileWidget,
    Form,
    HtmlWidget,
    RadiobuttonsWidget,
    RichTextWidget,
    SingleSelectWidget,
    SlugWidget,
    StringWidget,
    UrlWidget,
)
from wcs.sql_criterias import Equal
from wcs.workflows import (
    DuplicateGlobalActionNameError,
    DuplicateStatusNameError,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
    WorkflowGlobalAction,
    WorkflowImportError,
    WorkflowVariablesFieldsFormDef,
    item_classes,
)

from . import utils
from .comment_templates import CommentTemplatesDirectory
from .data_sources import NamedDataSourcesDirectory
from .documentable import DocumentableFieldMixin, DocumentableMixin
from .fields import FieldDefPage, FieldsDirectory
from .logged_errors import LoggedErrorsDirectory
from .mail_templates import MailTemplatesDirectory


def is_global_accessible():
    return get_publisher().get_backoffice_root().is_global_accessible('workflows')


def update_order(elements):
    request = get_request()
    new_order = request.form['order'].strip(';').split(';')
    new_elements = []
    for y in new_order:
        element = [x for x in elements if x.id == y]
        if not element:
            continue
        new_elements.append(element[0])
    if {element.id for element in new_elements} != {element.id for element in elements}:
        return None
    return new_elements


def svg(tag):
    return '{http://www.w3.org/2000/svg}%s' % tag


def xlink(tag):
    return '{http://www.w3.org/1999/xlink}%s' % tag


TITLE = svg('title')
POLYGON = svg('polygon')
XLINK_TITLE = xlink('title')


def remove_tag(node, tag):
    for child in node:
        if child.tag == tag:
            node.remove(child)


def remove_attribute(node, att):
    if att in node.attrib:
        del node.attrib[att]


def adjust_style(node, top, colours, white_text=False, colour_class=None):
    remove_tag(node, TITLE)
    if node.get('class') and node.get('class').startswith('node '):
        colour_class = node.get('class').split()[-1]
    if (node.get('fill'), node.get('stroke')) in (('white', 'white'), ('white', 'none')):
        # this is the general white background, reduce it to a dot
        node.attrib['points'] = '0,0 0,0 0,0 0,0'
    if node.tag == svg('text') and white_text:
        node.attrib['fill'] = 'white'
    for child in node:
        remove_attribute(child, XLINK_TITLE)
        if child.tag == '{http://www.w3.org/2000/svg}polygon' and colour_class:
            # for compatibility with graphviz >= 2.40 replace fill attribute
            # with the original colour name.
            child.attrib['fill'] = colour_class
        if child.get('fill') in colours:
            matching_hexa = colours.get(child.get('fill'))
            child.attrib['fill'] = matching_hexa
            del child.attrib['stroke']
            if misc.get_foreground_colour(matching_hexa) == 'white':
                white_text = True
        if child.get('font-family'):
            del child.attrib['font-family']
        if child.get('font-size'):
            child.attrib['font-size'] = str(float(child.attrib['font-size']) * 0.8)
        remove_attribute(child, 'style')
        adjust_style(child, top, colours, white_text=white_text, colour_class=colour_class)


def graphviz_post_treatment(content, colours, include=False):
    """Remove all svg:title and top-level svg:polygon nodes, remove style
    attributes and xlink:title attributes.

    If a color style is set to a name matching class-\\w+, set the second
    part on as class selector on the top level svg:g element.
    """
    tree = ET.fromstring(content)
    if not include:
        style = ET.SubElement(tree, svg('style'))
        style.attrib['type'] = 'text/css'
        css_url = '%s%s%s' % (
            get_publisher().get_root_url(),
            get_publisher().qommon_static_dir,
            get_publisher().qommon_admin_css,
        )
        style.text = '@import url(%s);' % css_url

    for root in tree:
        remove_tag(root, TITLE)
        for child in root:
            adjust_style(child, child, colours)
    return force_str(ET.tostring(tree))


def graphviz(workflow, url_prefix='', select=None, svg=True, include=False):
    out = io.StringIO()
    # a list of colours known to graphviz, they will serve as key to get back
    # to the colours defined in wcs, they are used as color attributes in
    # graphviz (<= 2.38) then as class attribute on node elements for 2.40 and
    # later.
    graphviz_colours = [
        'aliceblue',
        'antiquewhite',
        'aqua',
        'aquamarine',
        'azure',
        'beige',
        'bisque',
        'black',
        'blanchedalmond',
        'blue',
        'blueviolet',
        'brown',
        'burlywood',
        'cadetblue',
        'chartreuse',
        'chocolate',
        'coral',
        'cornflowerblue',
        'cornsilk',
        'crimson',
        'cyan',
        'darkblue',
        'darkcyan',
        'darkgoldenrod',
        'darkgray',
        'darkgreen',
        'darkgrey',
        'darkkhaki',
        'darkmagenta',
        'darkolivegreen',
        'darkorange',
        'darkorchid',
        'darkred',
        'darksalmon',
        'darkseagreen',
        'darkslateblue',
        'darkslategray',
        'darkslategrey',
        'darkturquoise',
        'darkviolet',
        'deeppink',
        'deepskyblue',
        'dimgray',
        'dimgrey',
        'dodgerblue',
        'firebrick',
        'floralwhite',
        'forestgreen',
        'fuchsia',
        'gainsboro',
        'ghostwhite',
        'gold',
        'goldenrod',
        'gray',
        'grey',
        'green',
        'greenyellow',
        'honeydew',
        'hotpink',
        'indianred',
        'indigo',
        'ivory',
        'khaki',
        'lavender',
        'lavenderblush',
        'lawngreen',
        'lemonchiffon',
        'lightblue',
        'lightcoral',
        'lightcyan',
        'lightgoldenrodyellow',
        'lightgray',
        'lightgrey',
        'lightpink',
    ]

    colours = {}
    revert_colours = {}
    print('digraph main {', file=out)
    # print >>out, 'graph [ rankdir=LR ];'
    print('node [shape=box,style=filled];', file=out)
    print('edge [];', file=out)
    for status in workflow.possible_status:
        i = status.id
        print('status%s' % i, end=' ', file=out)
        label = status.get_status_label().replace('"', "'")
        print('[label="%s"' % label, end=' ', file=out)
        if select == str(i):
            print(',id=current_status', file=out)
        classes = []
        if status.is_endpoint():
            classes.append('is-endpoint')
        elif status.is_waitpoint():
            classes.append('is-waitpoint')
        else:
            classes.append('is-transition')
        if status.colour:
            if status.colour not in colours:
                colours[status.colour] = graphviz_colours.pop()
                revert_colours[colours[status.colour]] = status.colour
            print(',color=%s' % colours[status.colour], file=out)
            classes.append(colours[status.colour])
        print(',class="%s"' % ' '.join(classes), file=out)
        print(' URL="%sstatus/%s/"];' % (url_prefix, i), file=out)

        loop_target_status = status.get_loop_target_status()
        if loop_target_status:
            url = 'status/%s/' % i
            print('status%s -> status%s' % (i, loop_target_status.id), file=out)
            label = status.get_loop_jump_label()
            label = label.replace('"', '\\"')
            label = textwrap.fill(label, 20, break_long_words=False)
            label = label.replace('\n', '\\n')
            label = label.replace('&', '&amp;')
            print('[label="%s"' % label, end=' ', file=out)
            print(',URL="%s%s"]' % (url_prefix, url), file=out)

    for status in workflow.possible_status:
        i = status.id
        for item in status.items:
            next_status_ids = [x.id for x in item.get_target_status() if x.id]
            if not next_status_ids:
                next_status_ids = [status.id]
            done = {}
            url = 'status/%s/items/%s/' % (i, item.id)
            for next_id in next_status_ids:
                if next_id in done:
                    # don't display multiple arrows for same action and target
                    # status
                    continue
                print('status%s -> status%s' % (i, next_id), file=out)
                done[next_id] = True
                label = item.get_jump_label(target_id=next_id)
                label = label.replace('"', '\\"')
                label = textwrap.fill(label, 20, break_long_words=False)
                label = label.replace('\n', '\\n')
                label = label.replace('&', '&amp;')
                print('[label="%s"' % label, end=' ', file=out)
                print(',URL="%s%s"]' % (url_prefix, url), file=out)

    print('}', file=out)
    out = out.getvalue()
    if svg:
        try:
            with Popen(['dot', '-Tsvg'], stdin=PIPE, stdout=PIPE) as process:
                out = process.communicate(force_bytes(out))[0]
                if process.returncode != 0:
                    return ''
        except OSError:
            return ''
        out = graphviz_post_treatment(out, revert_colours, include=include)
        if include:
            # It seems webkit refuse to accept SVG when using its proper namespace,
            # and xlink namespace prefix must be xlink: to be acceptable
            out = out.replace('ns0:', '')
            out = out.replace('xmlns:ns0', 'xmlns:svg')
            out = out.replace('ns1:', 'xlink:')
            out = out.replace(':ns1', ':xlink')
    out = out.replace('<title>main</title>', '<title>%s</title>' % workflow.name)
    return out


class WorkflowUI:
    def __init__(self, workflow):
        self.workflow = workflow

    def form_new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Workflow Name'), required=True, size=30)
        category_options = get_categories(WorkflowCategory)
        if category_options:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
            )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def form_edit(self):
        form = Form(enctype='multipart/form-data')
        form.add_hidden('id', value=self.workflow.id)

        kwargs = {}
        if self.workflow.slug == misc.simplify(self.workflow.name, force_letter_first=True):
            # if name and url name are in sync, keep them that way
            kwargs['data-slug-sync'] = 'slug'
        form.add(
            StringWidget,
            'name',
            title=_('Workflow Name'),
            required=True,
            size=30,
            value=self.workflow.name,
            **kwargs,
        )

        from wcs.applications import ApplicationElement

        disabled_slug = ApplicationElement.exists(
            [Equal('object_type', 'workflow'), Equal('object_id', str(self.workflow.id))]
        )
        kwargs = {}
        if disabled_slug:
            kwargs['readonly'] = 'readonly'
        form.add(
            SlugWidget,
            'slug',
            title=_('Identifier'),
            value=self.workflow.slug,
            **kwargs,
        )

        if disabled_slug:
            form.widgets.append(
                HtmlWidget(
                    '<p>%s<br>'
                    % _('The identifier should not be modified as the workflow is part of an application.')
                )
            )
            form.widgets.append(
                HtmlWidget(
                    '<a href="" class="change-nevertheless">%s</a></p>'
                    % _('I understand the danger, make it editable nevertheless.')
                )
            )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        if self.workflow:
            workflow = self.workflow
        else:
            workflow = Workflow(name=form.get_widget('name').parse())

        workflows = [x for x in Workflow.select() if str(x.id) != str(workflow.id)]
        name = form.get_widget('name').parse()
        if name in (x.name for x in workflows):
            form.get_widget('name').set_error(_('This name is already used.'))
            raise ValueError()

        if form.get_widget('slug'):
            slug = form.get_widget('slug').parse()
            if slug in (x.slug for x in workflows):
                form.get_widget('slug').set_error(_('This identifier is already used.'))
                raise ValueError()

        for f in ('name', 'slug', 'category_id'):
            widget = form.get_widget(f)
            if widget:
                setattr(workflow, f, widget.parse())
        workflow.store()
        return workflow


class WorkflowItemPage(Directory, DocumentableMixin):
    _q_exports = [
        '',
        'delete',
        'copy',
        ('update-documentation', 'update_documentation'),
    ]
    do_not_call_in_templates = True

    change_comment = _('Change in action "%(description)s" in status "%(name)s"')
    deletion_comment = _('Deletion of action "%(description)s" in status "%(name)s"')

    def __init__(self, workflow, parent, component):
        try:
            self.item = [x for x in parent.items if x.id == component][0]
        except (IndexError, ValueError):
            raise errors.TraversalError()
        self.workflow = workflow
        self.parent = parent
        self.documented_object = self.workflow
        self.documented_element = self.item
        get_response().breadcrumb.append(('items/%s/' % component, self.item.description))

    def _q_index(self):
        request = get_request()
        if request.get_method() == 'GET' and request.form.get('file'):
            value = getattr(self.item, request.form.get('file'), None)
            if value:
                return value.build_response()

        form = Form(enctype='multipart/form-data', use_tabs=True)
        self.item.fill_admin_form(form)

        if not self.workflow.is_readonly():
            submit_label = _('Submit')
            if hasattr(self.item, 'submit_button_label'):
                submit_label = self.item.submit_button_label
            form.add_submit('submit', submit_label)
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.get_submit() == 'submit' and not form.has_errors():
            self.item.submit_admin_form(form)
            if not form.has_errors():
                self.workflow.store(
                    self.change_comment
                    % {
                        'description': self.item.render_as_line(),
                        'name': self.parent.name,
                    }
                )
                if getattr(self.item, 'redirect_after_submit_url', None):
                    return redirect(self.item.redirect_after_submit_url)
                return redirect('..')

        get_response().set_title('%s - %s' % (_('Workflow'), self.workflow.name))
        get_response().add_javascript(['jquery-ui.js'])
        context = {
            'view': self,
            'html_form': form,
            'workflow': self.workflow,
            'has_sidebar': True,
            'action': self.item,
            'get_substitution_html_table': get_publisher().substitutions.get_substitution_html_table,
        }
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-action.html'],
            context=context,
            is_django_native=True,
        )

    def get_common_varnames(self):
        common_varnames = None
        for formdef in itertools.chain(self.workflow.formdefs(), self.workflow.carddefs()):
            varnames = set()
            for field in formdef.fields:
                if field.varname:
                    varnames.add(field.varname)
            if common_varnames is None:
                common_varnames = varnames
            else:
                common_varnames &= common_varnames.intersection(varnames)
        if common_varnames is None:
            common_varnames = set()
        if self.workflow.backoffice_fields_formdef and self.workflow.backoffice_fields_formdef.fields:
            for field in self.workflow.backoffice_fields_formdef.fields:
                if field.varname:
                    common_varnames.add(field.varname)
        return list(common_varnames)

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to remove an item.')))
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('../../')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Item'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Deleting Item')
            r += form.render()
            return r.getvalue()

        del self.parent.items[self.parent.items.index(self.item)]
        self.workflow.store(
            comment=self.deletion_comment
            % {
                'description': self.item.render_as_line(),
                'name': self.parent.name,
            }
        )
        return redirect('../../')

    def copy(self):
        form = Form(enctype='multipart/form-data')
        destinations = [(x.id, x.name) for x in self.workflow.possible_status]

        form.add(
            SingleSelectWidget, 'status', title=_('Target status'), options=destinations, value=self.parent.id
        )

        form.add_submit('copy', _('Copy'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('../../')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.copy_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('copy', _('Copy')))
        get_response().set_title(_('Copy Item'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Copy Item')
        r += form.render()
        return r.getvalue()

    def copy_submit(self, form):
        status_id = form.get_widget('status').parse()
        destination_status = self.workflow.get_status(status_id)

        item = self.item.export_to_xml()
        item_type = item.attrib['type']
        new_item = destination_status.add_action(item_type)
        new_item.parent = destination_status
        try:
            new_item.init_with_xml(item, check_datasources=False)
        except WorkflowImportError as e:
            reason = _(e.msg) % e.msg_args
            if hasattr(e, 'render'):
                reason = e.render()
            elif e.details:
                reason += ' [%s]' % e.details
            form.add_global_errors([reason])
            raise ValueError()

        self.workflow.store(
            comment=_(
                'Copy of action "%(description)s" from status "%(from_status)s" to status "%(destination_status)s"'
            )
            % {
                'description': self.item.render_as_line(),
                'from_status': self.parent.name,
                'destination_status': destination_status.name,
            }
        )
        return redirect('../../')

    def _q_lookup(self, component):
        t = self.item.q_admin_lookup(self.workflow, self.parent, component)
        if t:
            return t
        return Directory._q_lookup(self, component)


class WorkflowGlobalActionItemPage(WorkflowItemPage):
    change_comment = _('Change in action "%(description)s" in global action "%(name)s"')
    deletion_comment = _('Deletion of action "%(description)s" in global action "%(name)s"')


class GlobalActionTriggerPage(Directory):
    _q_exports = ['', 'delete']

    def __init__(self, workflow, action, component):
        try:
            self.trigger = [x for x in action.triggers if x.id == component][0]
        except (IndexError, ValueError):
            raise errors.TraversalError()
        self.workflow = workflow
        self.action = action
        self.status = action
        get_response().breadcrumb.append(('triggers/%s/' % component, _('Trigger')))

    def _q_index(self):
        form = self.trigger.form(self.workflow)
        if not self.workflow.is_readonly():
            form.add_submit('submit', _('Save'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.get_submit() == 'submit' and not form.has_errors():
            self.trigger.submit_admin_form(form)
            if not form.has_errors():
                self.workflow.store(
                    comment=_('Change in trigger of global action "%(name)s') % {'name': self.action.name}
                )
                return redirect('../../')

        get_response().set_title('%s - %s' % (_('Workflow'), self.workflow.name))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s - %s</h2>') % (self.workflow.name, self.action.name)
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to remove a trigger.')))
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('../../')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Trigger'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Deleting Trigger')
            r += form.render()
            return r.getvalue()

        del self.action.triggers[self.action.triggers.index(self.trigger)]
        self.workflow.store(
            comment=_('Deletion of trigger in global action "%s"') % {'name': self.action.name}
        )
        return redirect('../../')


class ToChildDirectory(Directory):
    _q_exports = ['']
    klass = None

    def __init__(self, workflow, status):
        self.workflow = workflow
        self.status = status

    def _q_lookup(self, component):
        return self.klass(self.workflow, self.status, component)

    def _q_index(self):
        return redirect('..')


class WorkflowItemsDir(ToChildDirectory):
    klass = WorkflowItemPage


class GlobalActionTriggersDir(ToChildDirectory):
    klass = GlobalActionTriggerPage


class GlobalActionItemsDir(ToChildDirectory):
    klass = WorkflowGlobalActionItemPage


class WorkflowStatusPage(Directory, DocumentableMixin):
    _q_exports = [
        '',
        'delete',
        ('unset-forced-endpoint', 'unset_forced_endpoint'),
        'newitem',
        ('items', 'items_dir'),
        'update_order',
        'edit',
        'reassign',
        'options',
        'fullscreen',
        ('schema.svg', 'svg'),
        'svg',
        ('update-documentation', 'update_documentation'),
    ]
    do_not_call_in_templates = True

    new_action_message = _('New action "%(description)s" in status "%(name)s"')
    order_change_message = _('Change in action order in status "%(name)s')

    def __init__(self, workflow, status_id):
        self.workflow = workflow
        try:
            self.status = [x for x in self.workflow.possible_status if x.id == status_id][0]
        except IndexError:
            raise errors.TraversalError()

        self.items_dir = WorkflowItemsDir(workflow, self.status)
        self.documented_object = self.workflow
        self.documented_element = self.status
        get_response().breadcrumb.append(('status/%s/' % status_id, self.status.name))

    def _q_index(self):
        get_response().set_title('%s - %s' % (_('Workflow'), self.workflow.name))
        get_response().add_javascript(
            [
                'jquery.js',
                'jquery-ui.js',
                'biglist.js',
                'svg-pan-zoom.js',
                'qommon.wysiwyg.js',
                'popup.js',
            ]
        )
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-status.html'],
            context={'view': self, 'workflow': self.workflow, 'status': self.status, 'has_sidebar': True},
            is_django_native=True,
        )

    def get_source_statuses(self):
        statuses = []
        for status in self.workflow.possible_status:
            if status is self.status:
                continue
            if status.loop_items_template and status.after_loop_status == self.status.id:
                statuses.append(status)
                continue
            for item in status.items:
                if self.status in item.get_target_status():
                    statuses.append(status)
                    break
        return statuses

    def get_source_global_actions(self):
        global_actions = []
        for global_action in self.workflow.global_actions:
            for item in global_action.items:
                if self.status in item.get_target_status():
                    global_actions.append(global_action)
                    break
        return global_actions

    def graphviz(self):
        return graphviz(self.workflow, url_prefix='../../', include=True, select='%s' % self.status.id)

    def fullscreen(self):
        get_response().add_javascript(['jquery.js', 'svg-pan-zoom.js', 'qommon.admin.js'])
        context = {
            'view': self,
            'workflow': self.workflow,
            'back_url': self.status.get_admin_url(),
        }
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-fullscreen-schema.html'],
            context=context,
            is_django_native=True,
        )

    def svg(self):
        response = get_response()
        response.set_content_type('image/svg+xml')
        root_url = get_publisher().get_application_static_files_root_url()
        css = root_url + get_publisher().qommon_static_dir + get_publisher().qommon_admin_css
        return graphviz(
            self.workflow, url_prefix='../../', include=False, select='%s' % self.status.id
        ).replace('?>', '?>\n<?xml-stylesheet href="%s" type="text/css"?>\n' % css)

    def is_item_available(self, item):
        return not item.is_disabled() and item.is_available(workflow=self.workflow)

    def get_new_item_form(self):
        form = Form(enctype='multipart/form-data', action='newitem', id='new-action-form')
        categories = [
            ('status-change', _('Change Status')),
            ('interaction', _('Interact')),
            ('formdata-action', _('Act on a Form/Card')),
            ('user-action', _('Act on User')),
        ]
        available_items = [x for x in item_classes if self.is_item_available(x)]
        available_items.sort(key=lambda x: misc.simplify(x.description))

        for category, category_label in categories:
            options = [
                (x.key, x(parent=self.status).description) for x in available_items if x.category == category
            ]
            form.add(
                SingleSelectWidget,
                'action-%s' % category,
                title=category_label,
                required=False,
                options=[(None, '')] + options,
            )
        form.add_submit('submit', _('Add'))
        return form

    def update_order(self):
        get_response().set_content_type('application/json')
        reordered_items = update_order(self.status.items)
        if reordered_items is None:
            return json.dumps({'err': 1})
        self.status.items = reordered_items
        self.workflow.store(comment=self.order_change_message % {'name': self.status.name})
        return json.dumps({'err': 0})

    def newitem(self):
        form = self.get_new_item_form()

        if not form.is_submitted() or form.has_errors():
            get_session().add_message(_('Submitted form was not filled properly.'))
            return redirect('.')

        for category in ('status-change', 'interaction', 'formdata-action', 'user-action'):
            action_type = form.get_widget('action-%s' % category).parse()
            if action_type:
                self.status.add_action(action_type)
                self.workflow.store(
                    comment=self.new_action_message
                    % {
                        'description': self.status.items[-1].description,
                        'name': self.status.name,
                    }
                )
                return redirect('.')

        get_session().add_message(_('Submitted form was not filled properly.'))
        return redirect('.')

    def delete(self):
        form = Form(enctype='multipart/form-data')
        if self.workflow.possible_status and len(self.workflow.possible_status) == 1:
            form.widgets.append(
                HtmlWidget(
                    htmltext('<div class="warningnotice"><p>%s</p></div>')
                    % _(
                        'It is not possible to remove this status as '
                        'the workflow would not have any status left.'
                    )
                )
            )
            form.add_submit('cancel', _('Cancel'))
            if form.is_submitted():
                return redirect('../../')
        else:
            form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to remove a status.')))
            form.add_submit('delete', _('Delete'))
            form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Status'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Status:'), self.status.name)
            r += form.render()
            return r.getvalue()

        # Before removing the status, scan formdata to know if it's in use.
        for formdef in itertools.chain(FormDef.select(lightweight=True), CardDef.select(lightweight=True)):
            if formdef.workflow_id != str(self.workflow.id):
                continue
            if formdef.data_class().exists([Equal('status', 'wf-%s' % self.status.id)]):
                return redirect('reassign')

        from wcs.applications import Application

        Application.load_for_object(self.workflow)
        if self.workflow.applications:
            # always reassign for application workflows
            return redirect('reassign')

        del self.workflow.possible_status[self.workflow.possible_status.index(self.status)]
        self.workflow.store(comment=_('Deletion of status %s') % self.status.name)
        return redirect('../../')

    def edit(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Status Name'), required=True, size=30, value=self.status.name)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            new_name = str(form.get_widget('name').parse())
            if [x for x in self.workflow.possible_status if x.name == new_name]:
                form.get_widget('name').set_error(_('There is already a status with that name.'))
            else:
                self.status.name = new_name
                self.workflow.store(comment=_('Change name of status %s') % new_name)
                return redirect('.')

        get_response().set_title(_('Edit Status Name'))
        get_response().breadcrumb.append(('edit', _('Edit')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Status Name')
        r += form.render()
        return r.getvalue()

    def reassign(self):
        carddefs = [x for x in CardDef.select(lightweight=True) if x.workflow_id == str(self.workflow.id)]
        formdefs = [x for x in FormDef.select(lightweight=True) if x.workflow_id == str(self.workflow.id)]

        from wcs.applications import Application

        Application.load_for_object(self.workflow)
        if self.workflow.applications:
            description = _(
                'This workflow is part of an application and may have cards/forms set to this status.'
            )
            remove_option_label = _('Remove cards/forms with this status')
            change_option_label = _('Change cards/forms status to "%s"')
        elif formdefs and carddefs:
            remove_option_label = _('Remove these cards/forms')
            change_option_label = _('Change these cards/forms status to "%s"')
            description = _('There are forms or cards set to this status.')
        elif carddefs:
            remove_option_label = _('Remove these cards')
            change_option_label = _('Change these cards status to "%s"')
            description = _('There are cards set to this status.')
        else:
            remove_option_label = _('Remove these forms')
            change_option_label = _('Change these forms status to "%s"')
            description = _('There are forms set to this status.')

        options = [('', _('Do nothing'), ''), ('remove', remove_option_label, 'remove')]
        for status in self.workflow.get_waitpoint_status():
            if status.id == self.status.id:
                continue
            options.append(
                (f'reassign-{status.id}', change_option_label % status.name, f'reassign-{status.id}')
            )

        form = Form(enctype='multipart/form-data')
        form.add(SingleSelectWidget, 'action', title=_('Pick an Action'), options=options)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.get_widget('action').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('reassign', _('Delete / Reassign')))
            get_response().set_title(_('Delete Status / Reassign'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Status:'), self.status.name)
            r += htmltext('<div class="remove-status-form">')
            r += htmltext('<p>%s %s</p>') % (
                description,
                _('They need to be changed before this status can be deleted.'),
            )

            if not self.workflow.applications:
                r += htmltext('<ul>')
                for formdef in itertools.chain(formdefs, carddefs):
                    count = formdef.data_class().count([Equal('status', 'wf-%s' % self.status.id)])
                    if count:
                        r += htmltext('<li>%s%s %s %s</li>') % (
                            formdef.name,
                            _(':'),
                            count,
                            formdef.item_name if count < 2 else formdef.item_name_plural,
                        )
                r += htmltext('</ul>')

            r += form.render()
            r += htmltext('</div>')
            return r.getvalue()

        action = form.get_widget('action').parse()

        if not self.workflow.status_remapping:
            self.workflow.status_remapping = {}
        del self.workflow.possible_status[self.workflow.possible_status.index(self.status)]
        self.workflow.status_remapping[str(self.status.id)] = {
            'action': action,
            'status': str(self.status.id),
            'timestamp': localtime().isoformat(),
        }
        self.workflow.store(comment=_('Removal of status %s') % self.status.name)

        job = StatusChangeJob(
            workflow_id=self.workflow.id,
            action=action,
            current_status=f'wf-{self.status.id}',
        )
        job.store()
        get_publisher().add_after_job(job)
        return redirect(job.get_processing_url())

    def options(self):
        form = Form(enctype='multipart/form-data', use_tabs=True)
        form.add(
            RadiobuttonsWidget,
            'visibility_mode',
            title=_('Display of status in history'),
            value=self.status.get_visibility_mode(),
            options=[
                ('all', _('Displayed to everyone'), 'all'),
                ('restricted', _('Displayed only in backoffice'), 'restricted'),
                ('hidden', _('Never displayed'), 'hidden'),
            ],
        )
        form.add(ColourWidget, 'colour', title=_('Colour in backoffice'), value=self.status.colour)
        form.add(
            StringWidget,
            'extra_css_class',
            title=_('Extra CSS for frontoffice style'),
            value=self.status.extra_css_class,
        )
        form.add(
            CheckboxWidget,
            'force_terminal_status',
            title=_('Force Terminal Status'),
            value=(self.status.forced_endpoint is True),
        )
        form.add(
            RichTextWidget,
            'backoffice_info_text',
            title=_('Information text for backoffice'),
            value=self.status.backoffice_info_text,
        )
        form.add(
            ComputedExpressionWidget,
            'loop_items_template',
            title=_('Template for items to be looped on'),
            value=self.status.loop_items_template,
            tab=('loop', _('Loop system')),
        )
        form.add(
            SingleSelectWidget,
            'after_loop_status',
            title=_('Status after loop'),
            value=self.status.after_loop_status,
            options=[(None, '---', '', {})] + self.workflow.get_possible_target_options(),
            tab=('loop', _('Loop system')),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.status.set_visibility_mode(form.get_widget('visibility_mode').parse())
            self.status.colour = form.get_widget('colour').parse() or 'ffffff'
            self.status.extra_css_class = form.get_widget('extra_css_class').parse()
            self.status.forced_endpoint = form.get_widget('force_terminal_status').parse()
            self.status.backoffice_info_text = form.get_widget('backoffice_info_text').parse()
            if form.get_widget('loop_items_template'):
                self.status.loop_items_template = form.get_widget('loop_items_template').parse()
            if form.get_widget('after_loop_status'):
                self.status.after_loop_status = form.get_widget('after_loop_status').parse()
            self.workflow.store(comment=_('Change in options of status "%s"') % self.status.name)
            return redirect('.')

        get_response().set_title(_('Status Options'))
        get_response().breadcrumb.append(('options', _('Status Options')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Change Status Options')
        r += form.render()
        return r.getvalue()

    def unset_forced_endpoint(self):
        self.status.forced_endpoint = False
        self.workflow.store(comment=_('Unset forced endpoint for status "%s"') % self.status.name)
        return redirect('.')


class WorkflowStatusDirectory(Directory):
    _q_exports = ['', ('new-status', 'new')]

    def __init__(self, workflow):
        self.workflow = workflow

    def _q_lookup(self, component):
        return WorkflowStatusPage(self.workflow, component)

    def _q_index(self):
        return redirect('..')

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        form.add_submit('submit', _('Add'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect(self.workflow.get_admin_url())

        if form.is_submitted() and not form.has_errors():
            name = form.get_widget('name').parse()
            try:
                self.workflow.add_status(name)
            except DuplicateStatusNameError:
                form.get_widget('name').set_error(_('There is already a status with that name.'))
            else:
                self.workflow.store(comment=_('New status "%s"') % name)
                return redirect(self.workflow.get_admin_url())

        get_response().breadcrumb.append(('new-status', _('New Status')))
        get_response().set_title(_('New Status'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Status')
        r += form.render()
        return r.getvalue()


class WorkflowVariablesFieldDefPage(FieldDefPage, DocumentableFieldMixin):
    section = 'workflows'
    blacklisted_attributes = ['condition', 'prefill', 'display_locations', 'anonymise']
    has_documentation = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.workflow = self.objectdef.workflow
        self.documented_object = self.workflow
        self.documented_element = self.field

    def form(self):
        form = super().form()
        # add default value widget
        if self.field.key in ('string', 'email', 'text', 'date', 'numeric'):
            form.add(
                self.field.widget_class,
                'default_value',
                title=_('Default Value'),
                required=False,
                hint=_(
                    'Value to be used when the option is left empty '
                    '(the option does not have to be marked as required).'
                ),
                value=getattr(self.field, 'default_value', None),
            )
        return form

    def submit(self, form):
        default_value_widget = form.get_widget('default_value')
        if default_value_widget:
            self.field.default_value = default_value_widget.parse()
        super().submit(form)


class WorkflowBackofficeFieldDefPage(FieldDefPage, DocumentableFieldMixin):
    section = 'workflows'
    blacklisted_attributes = ['condition']

    def get_sidebar(self):
        if not self.field.id:
            return
        usage_actions = []
        for action in self.objectdef.workflow.get_all_items():
            if action.key != 'set-backoffice-fields':
                continue
            if any(x.get('field_id') == self.field.id for x in action.fields or []):
                usage_actions.append(action)

        r = TemplateIO(html=True)
        r += self.documentation_part()
        if usage_actions:
            get_response().filter['sidebar_attrs'] = ''
            r += htmltext('<div class="actions-using-this-field">')
            r += htmltext('<h3>%s</h3>') % _('Actions using this field')
            r += htmltext('<ul>')
            for action in usage_actions:
                label = _('"%s" action') % action.label if action.label else _('Action')
                if isinstance(action.parent, WorkflowGlobalAction):
                    location = _('in global action "%s"') % action.parent.name
                else:
                    location = _('in status "%s"') % action.parent.name
                r += htmltext(f'<li><a href="{action.get_admin_url()}">%s %s</a></li>') % (label, location)
            r += htmltext('</ul>')
            r += htmltext('<div>')
        return r.getvalue()

    def form(self):
        form = super().form()
        form.remove('prefill')
        display_locations = form.get_widget('display_locations')
        if display_locations:
            # remove validation page from choices
            display_locations.options = display_locations.options[1:]
        return form

    def schedule_statistics_data_update(self):
        from wcs.formdef_jobs import UpdateStatisticsDataAfterJob

        formdefs = [
            x
            for x in FormDef.select(lightweight=True) + CardDef.select(lightweight=True)
            if x.workflow_id == str(self.objectdef.workflow.id)
        ]
        get_publisher().add_after_job(UpdateStatisticsDataAfterJob(formdefs=formdefs))


class WorkflowVariablesFieldsDirectory(FieldsDirectory):
    _q_exports = ['', 'update_order', 'new', ('update-documentation', 'update_documentation')]

    section = 'workflows'
    field_def_page_class = WorkflowVariablesFieldDefPage
    support_import = False
    blacklisted_types = ['page', 'blocks', 'computed']
    field_var_prefix = 'form_option_'
    readonly_message = _('This workflow is readonly.')
    new_field_history_message = _('New workflow option "%s"')

    field_count_message = _('This workflow contains %d variables.')
    field_over_count_message = _('This workflow contains more than %d variables.')

    def index_top(self):
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % self.objectdef.name
        r += htmltext('<span class="actions">%s</span>') % self.get_documentable_button()
        r += htmltext('</div>')
        r += get_session().display_message()
        r += self.get_documentable_zone()
        if not self.objectdef.fields:
            r += htmltext('<p>%s</p>') % _('There are not yet any variables.')
        return r.getvalue()

    def index_bottom(self):
        pass


class WorkflowBackofficeFieldsDirectory(FieldsDirectory):
    _q_exports = ['', 'update_order', 'new', ('update-documentation', 'update_documentation')]

    section = 'workflows'
    field_def_page_class = WorkflowBackofficeFieldDefPage
    support_import = False
    blacklisted_types = ['page', 'computed']
    blacklisted_attributes = ['condition']
    field_var_prefix = 'form_var_'
    readonly_message = _('This workflow is readonly.')
    new_field_history_message = _('New backoffice field "%s"')
    field_count_message = _('This workflow contains %d backoffice fields.')
    field_over_count_message = _('This workflow contains more than %d backoffice fields.')

    def __init__(self, objectdef):
        super().__init__(objectdef)
        self.documented_object = objectdef
        self.documented_element = objectdef

    def index_top(self):
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % self.objectdef.name
        r += htmltext('<span class="actions">%s</span>') % self.get_documentable_button()
        r += htmltext('</div>')
        r += get_session().display_message()
        r += self.get_documentable_zone()
        if not self.objectdef.fields:
            r += htmltext('<p>%s</p>') % _('There are not yet any backoffice fields.')
        return r.getvalue()

    def index_bottom(self):
        pass


class VariablesDirectory(Directory):
    _q_exports = ['', 'fields']

    def __init__(self, workflow):
        self.workflow = workflow

    def _q_index(self):
        return redirect('fields/')

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('variables/', _('Variables')))
        self.fields = WorkflowVariablesFieldsDirectory(WorkflowVariablesFieldsFormDef(self.workflow))
        return Directory._q_traverse(self, path)


class BackofficeFieldsDirectory(Directory):
    _q_exports = ['', 'fields']

    def __init__(self, workflow):
        self.workflow = workflow

    def _q_index(self):
        return redirect('fields/')

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('backoffice-fields/', _('Backoffice Fields')))
        self.fields = WorkflowBackofficeFieldsDirectory(WorkflowBackofficeFieldsFormDef(self.workflow))
        return Directory._q_traverse(self, path)


class FunctionsDirectory(Directory):
    _q_exports = ['', 'new']

    def __init__(self, workflow):
        self.workflow = workflow

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('functions/', _('Functions')))
        return Directory._q_traverse(self, path)

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        form.add_submit('submit', _('Add'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            name = form.get_widget('name').parse()
            base_slug = slug = '_%s' % misc.simplify(name)
            base_idx = 2
            while slug in self.workflow.roles:
                slug = '%s-%s' % (base_slug, base_idx)
                base_idx += 1
            self.workflow.roles[slug] = name
            self.workflow.store(comment=_('New function "%s"') % name)
            return redirect('..')

        get_response().breadcrumb.append(('new', _('New Function')))
        get_response().set_title(_('New Function'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Function')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        function = self.workflow.roles.get('_' + component)
        if not function:
            raise errors.TraversalError()

        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50, value=function)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if component != 'receiver':
            # do not allow removing the standard "receiver" function.
            # TODO: do not display "delete" for functions that are currently in
            # use.
            form.add_submit('delete', _('Delete'))

        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.get_submit() == 'delete':
            slug = '_%s' % component
            name = self.workflow.roles[slug]
            del self.workflow.roles[slug]
            self.workflow.store(comment=_('Deletion of function "%s"') % name)
            get_publisher().add_after_job(FunctionDeletionAfterJob(self.workflow, slug, name))
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            name = form.get_widget('name').parse()
            slug = '_%s' % component
            self.workflow.roles[slug] = name
            self.workflow.store(comment=_('Rename of function "%s"') % name)
            return redirect('..')

        get_response().breadcrumb.append(('new', _('Edit Function')))
        get_response().set_title(_('Edit Function'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Function')
        r += form.render()
        return r.getvalue()

    def _q_index(self):
        return redirect('..')


class CriticalityLevelsDirectory(Directory):
    _q_exports = ['', 'new']

    def __init__(self, workflow):
        self.workflow = workflow

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('criticality-levels/', _('Criticality Levels')))
        return Directory._q_traverse(self, path)

    def new(self):
        currentlevels = self.workflow.criticality_levels or []
        default_colours = ['#FFFFFF', '#FFFF00', '#FF9900', '#FF6600', '#FF0000']
        try:
            default_colour = default_colours[len(currentlevels)]
        except IndexError:
            default_colour = '#000000'
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        form.add(ColourWidget, 'colour', title=_('Colour'), required=False, value=default_colour)
        form.add_submit('submit', _('Add'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            if not self.workflow.criticality_levels:
                self.workflow.criticality_levels = []
            level = WorkflowCriticalityLevel()
            level.name = form.get_widget('name').parse()
            level.colour = form.get_widget('colour').parse()
            self.workflow.criticality_levels.append(level)
            self.workflow.store(comment=_('New criticality level'))
            return redirect('..')

        get_response().breadcrumb.append(('new', _('New Criticality Level')))
        get_response().set_title(_('New Criticality level'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Criticality Level')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        for level in self.workflow.criticality_levels or []:
            if level.id == component:
                break
        else:
            raise errors.TraversalError()

        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50, value=level.name)
        form.add(ColourWidget, 'colour', title=_('Colour'), required=False, value=level.colour)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        form.add_submit('delete-level', _('Delete'))

        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.get_submit() == 'delete-level':
            self.workflow.criticality_levels.remove(level)
            self.workflow.store(comment=_('Deletion of criticality level'))
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            level.name = form.get_widget('name').parse()
            level.colour = form.get_widget('colour').parse()
            self.workflow.store(comment=_('Change of name of criticality level'))
            return redirect('..')

        get_response().breadcrumb.append(('new', _('Edit Criticality Level')))
        get_response().set_title(_('Edit Criticality Level'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Criticality Level')
        r += form.render()
        return r.getvalue()

    def _q_index(self):
        return redirect('..')


class GlobalActionPage(WorkflowStatusPage):
    _q_exports = [
        '',
        'new',
        'delete',
        'newitem',
        ('items', 'items_dir'),
        'update_order',
        'edit',
        'newtrigger',
        ('triggers', 'triggers_dir'),
        'update_triggers_order',
        'options',
        ('update-documentation', 'update_documentation'),
    ]

    new_action_message = _('New action "%(description)s" in global action "%(name)s"')
    order_change_message = _('Change in action order in global action "%(name)s"')

    def __init__(self, workflow, action_id):
        self.workflow = workflow
        try:
            self.action = [x for x in self.workflow.global_actions if x.id == action_id][0]
        except IndexError:
            raise errors.TraversalError()
        self.status = self.action
        self.items_dir = GlobalActionItemsDir(workflow, self.action)
        self.triggers_dir = GlobalActionTriggersDir(workflow, self.action)
        self.documented_object = self.workflow
        self.documented_element = self.action

    def _q_traverse(self, path):
        get_response().breadcrumb.append(
            ('global-actions/%s/' % self.action.id, _('Global Action: %s') % self.action.name)
        )
        return Directory._q_traverse(self, path)

    def is_item_available(self, item):
        return not item.is_disabled() and item.is_available(self.workflow) and item.ok_in_global_action

    def _q_index(self):
        get_response().set_title('%s - %s' % (_('Workflow'), self.workflow.name))
        get_response().add_javascript(
            [
                'jquery.js',
                'jquery-ui.js',
                'biglist.js',
                'qommon.wysiwyg.js',
                'popup.js',
                'widget_list.js',
            ]
        )

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-global-action.html'],
            context={'view': self, 'workflow': self.workflow, 'action': self.action, 'has_sidebar': True},
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(
            snapshot=self.workflow.snapshot_object,
            url_prefix='../../../../',
            url_suffix='global-actions/%s/' % self.status.id,
        )

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to remove an action.')))
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Action'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Action:'), self.action.name)
            r += form.render()
            return r.getvalue()

        del self.workflow.global_actions[self.workflow.global_actions.index(self.action)]
        self.workflow.store(comment=_('Deletion of global action "%s"') % self.action.name)
        return redirect('../../')

    def edit(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Action Name'), required=True, size=30, value=self.action.name)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            new_name = str(form.get_widget('name').parse())
            if [x for x in self.workflow.global_actions if x.name == new_name]:
                form.get_widget('name').set_error(_('There is already an action with that name.'))
            else:
                self.action.name = new_name
                self.workflow.store(comment=_('Change in global action "%s"') % self.action.name)
                return redirect('.')

        get_response().set_title(_('Edit Action Name'))
        get_response().breadcrumb.append(('edit', _('Edit')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Action Name')
        r += form.render()
        return r.getvalue()

    def options(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            RichTextWidget,
            'backoffice_info_text',
            title=_('Information text for backoffice'),
            value=self.action.backoffice_info_text,
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.action.backoffice_info_text = form.get_widget('backoffice_info_text').parse()
            self.workflow.store(comment=_('Change in "%s" global action options') % self.action.name)
            return redirect('.')

        get_response().set_title(_('Global Action Options'))
        get_response().breadcrumb.append(('options', _('Global Action Options')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Change Global Action Options')
        r += form.render()
        return r.getvalue()

    def update_triggers_order(self):
        request = get_request()
        new_order = request.form['order'].strip(';').split(';')
        self.action.triggers = [[x for x in self.action.triggers if x.id == y][0] for y in new_order]
        self.workflow.store(comment=_('Change in trigger order in action %s') % self.action.name)
        return 'ok'

    def newtrigger(self):
        form = self.get_new_trigger_form()

        if not form.is_submitted() or form.has_errors():
            get_session().add_message(_('Submitted form was not filled properly.'))
            return redirect('.')

        if form.get_widget('type').parse():
            self.action.append_trigger(form.get_widget('type').parse())
        else:
            get_session().add_message(_('Submitted form was not filled properly.'))
            return redirect('.')

        self.workflow.store(comment=_('New trigger in global action "%s"') % self.action.name)
        return redirect('.')

    def get_new_trigger_form(self):
        form = Form(enctype='multipart/form-data', action='newtrigger')
        available_triggers = [
            ('timeout', _('Automatic')),
            ('manual', _('Manual')),
            ('webservice', _('External call')),
        ]
        form.add(SingleSelectWidget, 'type', title=_('Type'), required=True, options=available_triggers)
        form.add_submit('submit', _('Add'))
        return form


class GlobalActionsDirectory(Directory):
    _q_exports = ['', 'new']

    def __init__(self, workflow):
        self.workflow = workflow

    def _q_lookup(self, component):
        return GlobalActionPage(self.workflow, component)

    def _q_index(self):
        return redirect('..')

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        form.add_submit('submit', _('Add'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            name = form.get_widget('name').parse()
            try:
                action = self.workflow.add_global_action(name)
            except DuplicateGlobalActionNameError:
                form.get_widget('name').set_error(_('There is already an action with that name.'))
            else:
                self.workflow.store(comment=_('New global action "%s"') % name)
                return redirect('%s/' % action.id)

        get_response().breadcrumb.append(('new', _('New Global Action')))
        get_response().set_title(_('New Global Action'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Global Action')
        r += form.render()
        return r.getvalue()


class WorkflowPage(Directory, DocumentableMixin):
    _q_exports = [
        '',
        'edit',
        'category',
        'delete',
        'newstatus',
        ('status', 'status_dir'),
        'update_order',
        'duplicate',
        'export',
        'svg',
        ('variables', 'variables_dir'),
        'inspect',
        ('schema.svg', 'svg'),
        ('backoffice-fields', 'backoffice_fields_dir'),
        'update_actions_order',
        'update_criticality_levels_order',
        ('functions', 'functions_dir'),
        ('global-actions', 'global_actions_dir'),
        ('criticality-levels', 'criticality_levels_dir'),
        ('logged-errors', 'logged_errors_dir'),
        ('history', 'snapshots_dir'),
        ('fullscreen'),
        ('update-documentation', 'update_documentation'),
    ]
    do_not_call_in_templates = True

    def __init__(self, component, instance=None):
        if instance:
            self.workflow = instance
        elif component == '_carddef_default':
            self.workflow = CardDef.get_default_workflow()
        else:
            try:
                self.workflow = Workflow.get(component)
            except KeyError:
                raise errors.TraversalError()
        self.workflow_ui = WorkflowUI(self.workflow)
        self.status_dir = WorkflowStatusDirectory(self.workflow)
        self.variables_dir = VariablesDirectory(self.workflow)
        self.backoffice_fields_dir = BackofficeFieldsDirectory(self.workflow)
        self.functions_dir = FunctionsDirectory(self.workflow)
        self.global_actions_dir = GlobalActionsDirectory(self.workflow)
        self.criticality_levels_dir = CriticalityLevelsDirectory(self.workflow)
        self.logged_errors_dir = LoggedErrorsDirectory(parent_dir=self, workflow_id=self.workflow.id)
        self.snapshots_dir = SnapshotsDirectory(self.workflow)
        self.documented_object = self.workflow
        self.documented_element = self.workflow
        if component:
            get_response().breadcrumb.append((component + '/', self.workflow.name))

    def category(self):
        category_options = get_categories(WorkflowCategory)
        form = Form(enctype='multipart/form-data')
        if category_options:
            form.widgets.append(HtmlWidget('<p>%s</p>' % _('Select a category for this workflow.')))
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                value=self.workflow.category_id,
                options=category_options,
            )
            if not self.workflow.is_readonly():
                form.add_submit('submit', _('Submit'))
        else:
            form.widgets.append(HtmlWidget('<p>%s</p>' % _('There are not yet any category.')))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            widget = form.get_widget('category_id')
            old_value = self.workflow.category_id
            new_value = widget.parse()
            if new_value != old_value:
                self.workflow.category_id = new_value
                self.workflow.store(comment=_('Change of category'))
            return redirect('.')

        get_response().set_title(self.workflow.name)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Category')
        r += form.render()
        return r.getvalue()

    def last_modification_block(self):
        return utils.last_modification_block(obj=self.workflow)

    def graphviz(self):
        return graphviz(self.workflow, include=True)

    def fullscreen(self):
        get_response().add_javascript(['jquery.js', 'svg-pan-zoom.js', 'qommon.admin.js'])
        context = {
            'view': self,
            'workflow': self.workflow,
            'back_url': self.workflow.get_admin_url(),
        }
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-fullscreen-schema.html'],
            context=context,
            is_django_native=True,
        )

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Workflow - %s') % self.workflow.name)
        get_response().add_javascript(['popup.js', 'biglist.js', 'svg-pan-zoom.js'])
        if not self.workflow.is_readonly():
            Application.load_for_object(self.workflow)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow.html'],
            context={'view': self, 'workflow': self.workflow, 'has_sidebar': True},
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(snapshot=self.workflow.snapshot_object)

    def errors_block(self):
        return LoggedErrorsDirectory.errors_block(workflow_id=self.workflow.id)

    def inspect(self):
        get_response().set_title(self.workflow.name)
        get_response().breadcrumb.append(('inspect', _('Inspector')))
        return self.render_inspect()

    def render_inspect(self):
        deprecations = DeprecationsDirectory()
        context = {
            'workflow': self.workflow,
            'view': self,
            'has_sidebar': self.workflow.is_readonly() and not self.workflow.is_default(),
            'deprecation_metadata': deprecations.metadata,
        }
        if not hasattr(self.workflow, 'snapshot_object'):
            context.update(
                {
                    'deprecations': deprecations.get_deprecations(f'workflow:{self.workflow.id}'),
                    'deprecation_metadata': deprecations.metadata,
                }
            )
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-inspect.html'],
            context=context,
            is_django_native=True,
        )

    def snapshot_info_inspect_block(self):
        return utils.snapshot_info_block(
            snapshot=self.workflow.snapshot_object, url_name='inspect', url_prefix='../'
        )

    def svg(self):
        response = get_response()
        response.set_content_type('image/svg+xml')
        root_url = get_publisher().get_application_static_files_root_url()
        css = root_url + get_publisher().qommon_static_dir + get_publisher().qommon_admin_css
        return graphviz(self.workflow, include=False).replace(
            '?>', '?>\n<?xml-stylesheet href="%s" type="text/css"?>\n' % css
        )

    def export(self):
        return misc.xml_response(
            self.workflow,
            filename='workflow-%s.wcs' % misc.simplify(self.workflow.name),
            content_type='application/x-wcs-workflow',
        )

    def update_order(self):
        get_response().set_content_type('application/json')
        new_possible_status = update_order(self.workflow.possible_status)
        if new_possible_status is None:
            return json.dumps({'err': 1})
        self.workflow.possible_status = new_possible_status
        self.workflow.store(comment=_('Change in status order'))
        return json.dumps({'err': 0})

    def update_actions_order(self):
        get_response().set_content_type('application/json')
        new_global_actions = update_order(self.workflow.global_actions)
        if new_global_actions is None:
            return json.dumps({'err': 1})
        self.workflow.global_actions = new_global_actions
        self.workflow.store(comment=_('Change in global actions order'))
        return json.dumps({'err': 0})

    def update_criticality_levels_order(self):
        get_response().set_content_type('application/json')
        new_criticality_levels = update_order(self.workflow.criticality_levels)
        if new_criticality_levels is None:
            return json.dumps({'err': 1})
        self.workflow.criticality_levels = new_criticality_levels
        self.workflow.store(comment=_('Change in criticality levels order'))
        return json.dumps({'err': 0})

    def edit(self):
        form = self.workflow_ui.form_edit()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                self.workflow_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().set_title(_('Edit Workflow'))
        r = TemplateIO(html=True)
        get_response().breadcrumb.append(('edit', _('Edit')))
        r += htmltext('<h2>%s</h2>') % _('Edit Workflow')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        from itertools import chain

        for objdef in chain(FormDef.select(), CardDef.select()):
            if objdef.workflow_id == str(self.workflow.id):
                form.widgets.append(
                    HtmlWidget('<p>%s</p>' % _('This workflow is currently in use, you cannot remove it.'))
                )
                form.add_submit('cancel', _('Cancel'))
                break
        else:
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this workflow.'))
            )
            form.add_submit('delete', _('Delete'))
            form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Workflow'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Workflow:'), self.workflow.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.workflow)
        self.workflow.remove_self()
        return redirect('..')

    def duplicate(self):
        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not is_global_accessible() and self.workflow.id in ('_default', '_carddef_default'):
            category_options = get_categories(WorkflowCategory)
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
            )

        if not form.is_submitted():
            original_name = self.workflow_ui.workflow.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            names = [x.name for x in Workflow.select()]
            no = 2
            while new_name in names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if form.is_submitted() and not form.has_errors():
            try:
                return self.duplicate_submit(form)
            except ValueError:
                pass

        get_response().set_title(_('Duplicate Workflow'))
        r = TemplateIO(html=True)
        get_response().breadcrumb.append(('duplicate', _('Duplicate')))
        r += htmltext('<h2>%s</h2>') % _('Duplicate Workflow')
        r += form.render()
        return r.getvalue()

    def duplicate_submit(self, form):
        # duplicate via xml export and import to get clean copy of
        # inner actions.
        tree = self.workflow_ui.workflow.export_to_xml(include_id=True)

        try:
            new_workflow = Workflow.import_from_xml_tree(tree, check_datasources=False)
        except WorkflowImportError as e:
            reason = _(e.msg) % e.msg_args
            if hasattr(e, 'render'):
                reason = e.render()
            elif e.details:
                reason += ' [%s]' % e.details
            form.add_global_errors([reason])
            raise ValueError()

        new_workflow.name = form.get_widget('name').parse()
        new_workflow.slug = None
        if form.get_widget('category_id'):
            new_workflow.category_id = form.get_widget('category_id').parse()
        new_workflow.store()

        return redirect('../%s/' % new_workflow.id)


class NamedDataSourcesDirectoryInWorkflows(NamedDataSourcesDirectory):
    pass


class WorkflowsDirectory(Directory):
    _q_exports = [
        '',
        'new',
        'categories',
        ('import', 'p_import'),
        ('data-sources', 'data_sources'),
        ('mail-templates', 'mail_templates'),
        ('comment-templates', 'comment_templates'),
        ('application', 'applications_dir'),
        ('by-slug', 'by_slug'),
    ]

    data_sources = NamedDataSourcesDirectoryInWorkflows()
    mail_templates = MailTemplatesDirectory()
    comment_templates = CommentTemplatesDirectory()
    category_class = WorkflowCategory
    categories = WorkflowCategoriesDirectory()
    by_slug = utils.BySlugDirectory(klass=Workflow)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(Workflow)

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('workflows/', _('Workflows')))
        get_response().set_backoffice_section('workflows')
        return super()._q_traverse(path)

    def is_accessible(self, user, traversal=False):
        if is_global_accessible():
            return True

        # check for access to specific categories
        user_roles = set(user.get_roles())
        for category in WorkflowCategory.select():
            management_roles = {x.id for x in getattr(category, 'management_roles') or []}
            if management_roles and user_roles.intersection(management_roles):
                return True

        return False

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Workflows'))
        get_response().add_javascript(['popup.js'])

        context = {
            'view': self,
            'is_global_accessible': is_global_accessible(),
            'applications': Application.select_for_object_type(Workflow.xml_root_node),
            'elements_label': Workflow.verbose_name_plural,
            'has_sidebar': True,
        }
        workflows = Workflow.select(order_by='name')
        Application.populate_objects(workflows)
        context.update(self.get_list_context(workflows))

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflows.html'], context=context, is_django_native=True
        )

    def get_list_context(self, workflow_qs, application=False):
        formdef_workflows = [Workflow.get_default_workflow()]
        workflows_in_formdef_use = set(formdef_workflows[0].id)
        for formdef in FormDef.select(lightweight=True):
            workflows_in_formdef_use.add(str(formdef.workflow_id))

        carddef_workflows = [CardDef.get_default_workflow()]
        workflows_in_carddef_use = set(carddef_workflows[0].id)
        for carddef in CardDef.select(lightweight=True):
            workflows_in_carddef_use.add(str(carddef.workflow_id))

        shared_workflows = []
        unused_workflows = []
        if application:
            workflows = []
        else:
            workflows = formdef_workflows + carddef_workflows

        for workflow in workflow_qs:
            if str(workflow.id) in workflows_in_formdef_use and str(workflow.id) in workflows_in_carddef_use:
                shared_workflows.append(workflow)
            elif str(workflow.id) in workflows_in_formdef_use:
                formdef_workflows.append(workflow)
            elif str(workflow.id) in workflows_in_carddef_use:
                carddef_workflows.append(workflow)
            if str(workflow.id) in workflows_in_formdef_use or str(workflow.id) in workflows_in_carddef_use:
                workflows.append(workflow)
            else:
                unused_workflows.append(workflow)

        categories = WorkflowCategory.select_for_user()
        self.category_class.sort_by_position(categories)

        default_category = WorkflowCategory()
        default_category.id = '_default_category'
        for workflow in workflows:
            if workflow.id in ('_default', '_carddef_default'):
                workflow.category_id = default_category.id
        categories = [default_category] + categories

        if is_global_accessible():
            if len(categories) > 1:
                # if there are categorised workflows, add an explicit uncategorised
                # category
                uncategorised_category = WorkflowCategory(_('Uncategorised'))
            else:
                # otherwise just add a "silent" category
                uncategorised_category = WorkflowCategory('')
            uncategorised_category.id = '_uncategorised'
            categories = categories + [uncategorised_category]

        for workflow in workflows:
            if workflow in shared_workflows:
                workflow.css_class = 'shared-workflow'
                workflow.usage_label = _('Forms and card models')
            elif workflow in formdef_workflows:
                workflow.css_class = 'formdef-workflow'
                workflow.usage_label = _('Forms')
            elif workflow in carddef_workflows:
                workflow.css_class = 'carddef-workflow'
                workflow.usage_label = _('Card models')

        for workflow in unused_workflows:
            workflow.css_class = 'unused-workflow'
            if carddef_workflows:
                workflow.usage_label = _('Unused')

        for category in categories:
            if category.id == '_uncategorised':
                category.objects = [x for x in workflows + unused_workflows if not x.category_id]
            else:
                category.objects = [
                    x for x in workflows + unused_workflows if x.category_id == str(category.id)
                ]

        return {
            'categories': categories,
        }

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        workflow_ui = WorkflowUI(None)

        form = workflow_ui.form_new()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                workflow = workflow_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('%s/' % workflow.id)

        get_response().set_title(_('New Workflow'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Workflow')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        directory = WorkflowPage(component)
        if directory.workflow.id in ('_default', '_carddef_default'):
            return directory
        if not directory.workflow.has_admin_access(get_request().user):
            raise errors.AccessForbiddenError()
        return directory

    def p_import(self):
        form = Form(enctype='multipart/form-data')

        form.add(FileWidget, 'file', title=_('File'), required=False)
        form.add(UrlWidget, 'url', title=_('Address'), required=False, size=50)
        form.add_submit('submit', _('Import Workflow'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(_('Import Workflow'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Import Workflow')
        r += htmltext('<p>%s</p>') % _(
            'You can install a new workflow by uploading a file or by pointing to the workflow URL.'
        )
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        url = None
        if form.get_widget('file').parse():
            fp = form.get_widget('file').parse().fp
        elif form.get_widget('url').parse():
            url = form.get_widget('url').parse()
            try:
                fp = misc.urlopen(url)
            except misc.ConnectionError as e:
                form.set_error('url', _('Error loading form (%s).') % str(e))
                raise ValueError()
        else:
            form.set_error('file', _('You have to enter a file or a URL.'))
            raise ValueError()

        error, reason = False, None
        try:
            workflow = Workflow.import_from_xml(fp, check_deprecated=True)
        except WorkflowImportError as e:
            error = True
            reason = _(e.msg) % e.msg_args
            if hasattr(e, 'render'):
                form.add_global_errors([e.render()])
            elif e.details:
                reason += ' [%s]' % e.details
        except ValueError:
            error = True

        if not error:
            global_access = is_global_accessible()
            if not global_access:
                management_roles = {x.id for x in getattr(workflow.category, 'management_roles', None) or []}
                user_roles = set(get_request().user.get_roles())
                if not user_roles.intersection(management_roles):
                    error = True
                    reason = _('unauthorized category')

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            if url:
                form.set_error('url', msg)
            else:
                form.set_error('file', msg)
            raise ValueError()

        initial_workflow_name = workflow.name
        workflow_names = [x.name for x in Workflow.select()]
        copy_no = 1
        while workflow.name in workflow_names:
            if copy_no == 1:
                workflow.name = _('Copy of %s') % initial_workflow_name
            else:
                workflow.name = _('Copy of %(name)s (%(no)d)') % {
                    'name': initial_workflow_name,
                    'no': copy_no,
                }
            copy_no += 1
        if url:
            workflow.import_source_url = url
        workflow.store()
        get_session().add_message(_('This workflow has been successfully imported.'), level='info')
        return redirect('%s/' % workflow.id)


class StatusChangeJob(AfterJob):
    def __init__(self, workflow_id, action, current_status):
        super().__init__(
            label=_('Updating data after workflow change'),
            workflow_id=workflow_id,
            action=action,
            current_status=current_status,
        )

    def execute(self):
        workflow_id = str(self.kwargs['workflow_id'])
        current_status = self.kwargs['current_status']
        action = self.kwargs['action']

        if action == 'nothing':
            return

        if action.startswith('reassign-'):
            new_status = 'wf-%s' % str(action)[9:]

        for formdef in itertools.chain(FormDef.select(), CardDef.select()):
            if formdef.workflow_id != workflow_id:
                continue
            for item in formdef.data_class().select([Equal('status', current_status)]):
                if action == 'remove':
                    item.remove_self()
                else:
                    item.status = new_status
                    evo = Evolution(formdata=item)
                    evo.time = localtime()
                    evo.status = new_status
                    evo.comment = str(_('Administrator reassigned status'))
                    if not item.evolution:
                        item.evolution = []
                    item.evolution.append(evo)
                    item.store()
            # delete all (old) status references in evolutions
            for item in formdef.data_class().select():
                if item.evolution:
                    modified = False
                    for evo in item.evolution:
                        if evo.status == self.status:
                            evo.status = None
                            modified = True
                    if modified:
                        item._store_all_evolution = True
                        item.store()

    def done_action_url(self):
        workflow = Workflow.get(self.kwargs['workflow_id'])
        return workflow.get_admin_url()

    def done_action_label(self):
        return _('Back')


class FunctionDeletionAfterJob(AfterJob):
    label = _('Updating forms after function removal')

    def __init__(self, workflow, slug, name):
        super().__init__()
        self.workflow_id = workflow.id
        self.slug = slug
        self.name = name

    def execute(self):
        workflow = Workflow.get(self.workflow_id)
        for formdef in itertools.chain(workflow.formdefs(), workflow.carddefs()):
            if self.slug in (formdef.workflow_roles or {}):
                del formdef.workflow_roles[self.slug]
                formdef.store(comment=_('Deletion of function "%s" in workflow') % self.name)
