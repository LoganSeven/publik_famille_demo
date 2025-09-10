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

import copy
import hashlib
import io
import json
import time
import urllib.parse

try:
    import qrcode
except ImportError:
    qrcode = None

import ratelimit.utils
from django.utils.http import quote
from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_response, get_session, get_session_manager, redirect
from quixote.directory import AccessControlled, Directory
from quixote.errors import MethodNotAllowedError, RequestError
from quixote.form import FormTokenWidget
from quixote.html import TemplateIO, htmltext
from quixote.util import randbytes

from wcs import sql
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.clamd import add_clamd_scan_job
from wcs.fields import MissingBlockFieldError, PageField, SetValueError
from wcs.formdata import FormData
from wcs.formdef import FormDef
from wcs.forms.common import FormStatusPage, FormTemplateMixin, TempfileDirectoryMixin
from wcs.qommon.admin.texts import TextsDirectory
from wcs.qommon.storage import NothingToUpdate
from wcs.roles import logged_users_role
from wcs.sql_criterias import Equal, NotEqual
from wcs.tracking_code import TrackingCode
from wcs.utils import record_timings
from wcs.variables import LazyFormData, LazyFormDef
from wcs.wf.editable import EditableWorkflowStatusItem
from wcs.workflows import ContentSnapshotPart, WorkflowStatusItem

from ..qommon import _, errors, get_cfg, misc, template
from ..qommon.form import ErrorMessage, Form, HiddenErrorWidget, HtmlWidget, StringWidget
from ..qommon.template import TemplateError
from ..qommon.template_utils import render_block_to_string
from ..utils import add_timing_mark


class SubmittedDraftException(Exception):
    pass


class RequiredUserException(Exception):
    pass


def tryauth(url):
    # tries to log the user in before redirecting to the asked url; this won't
    # do anything for local logins but will use a passive SAML request when
    # configured to use an external identity provider.
    if get_request().user:
        return redirect(url)
    ident_methods = get_cfg('identification', {}).get('methods', ['idp'])
    if 'idp' not in ident_methods:
        # when configured with local logins and not logged in, redirect to
        # asked url.
        return redirect(url)
    login_url = '/login/?ReturnUrl=%s&IsPassive=true' % quote(url)
    return redirect(login_url)


def auth(url):
    # logs the user in before redirecting to asked url.
    if get_request().user:
        return redirect(url)
    login_url = '/login/?ReturnUrl=%s' % quote(url)
    return redirect(login_url)


def forceauth(url):
    login_url = '/login/?ReturnUrl=%s&forceAuthn=true' % quote(url)
    return redirect(login_url)


class TrackingCodeDirectory(Directory):
    _q_exports = ['load']
    do_not_call_in_templates = True

    def __init__(self, code, formdef):
        self.code = code
        self.formdef = formdef

    @classmethod
    def get_formdata_from_code(cls, code):
        if get_request().is_from_bot():
            raise errors.AccessForbiddenError()
        rate_limit_option = get_publisher().get_site_option('rate-limit') or '3/s 1500/d'
        if rate_limit_option != 'none':
            for rate_limit in rate_limit_option.split():
                ratelimited = ratelimit.utils.is_ratelimited(
                    request=get_request().django_request,
                    group='trackingcode',
                    key='ip',
                    rate=rate_limit,
                    increment=True,
                )
                if ratelimited:
                    raise errors.AccessForbiddenError(_('Rate limit reached (%s).') % rate_limit)
        try:
            tracking_code = TrackingCode.get(code)
            if tracking_code.formdata_id is None:
                # this tracking code was not associated with any data; return a 404
                raise KeyError
            formdata = tracking_code.formdata
        except KeyError:
            raise errors.TraversalError()
        return formdata

    def load(self):
        bypass_checks = False
        if len(self.code) == 64:  # token
            try:
                token = get_publisher().token_class.get(self.code)
            except KeyError:
                raise errors.TraversalError()
            if token.type != 'temporary-access-url':
                raise errors.TraversalError()
            try:
                formdef = FormDef.get_by_urlname(token.context['form_slug'])
                formdata = formdef.data_class().get(token.context['form_number_raw'])
            except KeyError:
                raise errors.TraversalError()
            bypass_checks = token.context.get('bypass_checks')
            if token.context.get('backoffice'):
                get_session().mark_anonymous_formdata(formdata)
                return redirect(formdata.get_backoffice_url())
        elif get_publisher().get_site_option('allow-tracking-code-in-url') == 'true':
            formdata = self.get_formdata_from_code(self.code)
        else:
            raise errors.AccessForbiddenError()

        if not bypass_checks:
            if formdata.formdef.enable_tracking_codes is False:
                raise errors.TraversalError()
            if formdata.anonymised:
                raise errors.TraversalError()

        if formdata.is_submitter(get_request().user):
            return redirect(formdata.get_url())

        if not bypass_checks:
            verify_fields = []
            for field in formdata.formdef.fields:
                if field.id in (formdata.formdef.tracking_code_verify_fields or []):
                    if formdata.status == 'draft' and not formdata.data.get(field.id):
                        # a draft could be incomplete: do not test its empty values
                        continue
                    verify_fields.append(field)
            if verify_fields:
                form = Form()
                for field in verify_fields:
                    if field.key == 'computed':
                        # mock a add_to_form for a ComputedField
                        form.add(StringWidget, 'f%s' % field.id, title=field.label, size=25)
                        widget = form.get_widget('f%s' % field.id)
                        widget.div_id = 'var_%s' % field.varname
                    else:
                        widget = field.add_to_form(form)
                    widget.field = field
                form.add_submit('submit', _('Verify'))
                form.add_submit('cancel', _('Cancel'))

                if form.get_submit() == 'cancel':
                    return redirect('/')

                bad_content = False
                if form.is_submitted() and not form.has_errors():
                    for field in verify_fields:
                        value = formdata.data.get(field.id)
                        verify_value = form.get_widget('f%s' % field.id).parse()
                        if field.convert_value_from_str:
                            verify_value = field.convert_value_from_str(verify_value)
                        if (
                            isinstance(value, str)
                            and isinstance(verify_value, str)
                            and not get_publisher().has_site_option(
                                'use-strict-check-for-verification-fields'
                            )
                        ):
                            value = misc.simplify(value)
                            verify_value = misc.simplify(verify_value)
                        if value != verify_value:
                            # global error: we do not specify which field is in error, for security
                            form.add_global_errors(
                                [_('Access denied: this content does not match the form.')]
                            )
                            bad_content = True
                            break

                if not form.is_submitted() or form.has_errors() or bad_content:
                    get_response().set_title(_('Access rights verification'))
                    return template.QommonTemplateResponse(
                        templates=['wcs/tracking-code-data-check.html'],
                        context={
                            'html_form': form,
                            'verify_fields': verify_fields,
                        },
                    )

        get_session().mark_anonymous_formdata(formdata)
        return redirect(formdata.get_url())


class TrackingCodesDirectory(Directory):
    _q_exports = ['load']

    def __init__(self, formdef=None):
        self.formdef = formdef

    def load(self):
        if get_request().get_method() != 'POST':
            raise MethodNotAllowedError(allowed_methods=['POST', 'PUT'])
        code = get_request().form.get('code')
        if not code:
            raise RequestError(_('Missing code parameter.'))
        formdata = TrackingCodeDirectory.get_formdata_from_code(code)
        return redirect(formdata.get_temporary_access_url(duration=300))

    def _q_lookup(self, component):
        return TrackingCodeDirectory(component, self.formdef)


class FormPage(Directory, TempfileDirectoryMixin, FormTemplateMixin):
    # noqa pylint: disable=too-many-public-methods
    _q_exports = [
        '',
        'tempfile',
        'schema',
        'tryauth',
        'auth',
        'forceauth',
        'qrcode',
        'autosave',
        'code',
        'removedraft',
        'live',
        ('live-validation', 'live_validation'),
        ('go-to-backoffice', 'go_to_backoffice'),
    ]

    do_not_call_in_templates = True
    ensure_parent_category_in_url = True
    filling_templates = ['wcs/front/formdata_filling.html', 'wcs/formdata_filling.html']
    validation_templates = ['wcs/front/formdata_validation.html', 'wcs/formdata_validation.html']
    steps_templates = ['wcs/front/formdata_steps.html', 'wcs/formdata_steps.html']
    sidebox_templates = ['wcs/front/formdata_sidebox.html', 'wcs/formdata_sidebox.html']
    formdef_class = FormDef
    preview_mode = False
    edit_mode_submit_label = _('Save Changes')
    edit_mode_cancel_url = '.'
    edit_mode_return_url = None
    already_submitted_message = _('This form has already been submitted.')

    def __init__(self, component, parent_category=None, update_breadcrumbs=True):
        try:
            self.formdef = self.formdef_class.get_by_urlname(component)
        except KeyError:
            raise errors.TraversalError()

        self.parent_category = parent_category
        self.substvars = {}
        get_publisher().substitutions.feed(self)
        get_publisher().substitutions.feed(self.formdef)

        self.code = TrackingCodesDirectory(self.formdef)
        self.action_url = '.'
        self.edit_mode = False
        self.edit_action = None
        self.on_validation_page = False
        self.current_page = None
        self.user = get_request().user
        if update_breadcrumbs:
            get_response().breadcrumb.append((component + '/', get_publisher().translate(self.formdef.name)))

    def __call__(self):
        # add missing trailing slash.
        url = get_request().get_path() + '/'
        if get_request().get_query():
            url += '?' + get_request().get_query()
        return redirect(url)

    def get_substitution_variables(self):
        return self.substvars

    def schema(self):
        # backward compatibility
        from wcs.api import ApiFormdefDirectory

        return ApiFormdefDirectory(self.formdef).schema()

    def go_to_backoffice(self):
        return redirect(self.formdef.get_admin_url())

    def check_access(self):
        if self.formdef.roles:
            if not self.user:
                raise errors.AccessUnauthorizedError()
            if logged_users_role().id not in self.formdef.roles and not (self.user and self.user.is_admin):
                if self.user:
                    user_roles = set(self.user.get_roles())
                else:
                    user_roles = set()
                other_roles = self.formdef.roles or []
                if self.formdef.workflow_roles:
                    other_roles.extend(self.formdef.workflow_roles.values())
                if not user_roles.intersection(other_roles):
                    raise errors.AccessForbiddenError()

    def has_confirmation_page(self):
        if self.formdef.confirmation:
            return True
        if self.formdef.has_captcha_enabled():
            session = get_session()
            if not (session.get_user() or session.won_captcha):
                return True
        return False

    def has_draft_support(self):
        if self.edit_mode:
            return False
        if self.formdef.enable_tracking_codes:
            return True
        session = get_session()
        return session.has_user()

    def get_current_page_no(self):
        for i, page in enumerate(self.pages):
            if page is self.current_page:
                return i + 1
        return 0

    def step_context(self):
        page_labels = []
        current_position = 1

        for i, page in enumerate(self.pages):
            if page is None:  # monopage form
                page_labels.append(_('Filling'))
            else:
                page_labels.append(get_publisher().translate(page.label))
            if page is self.current_page:
                current_position = i + 1

        if self.has_confirmation_page() and not self.edit_mode:
            page_labels.append(_('Validating'))
            if self.on_validation_page:
                current_position = len(page_labels)

        return {
            'current_page_index0': current_position - 1,
            'current_page_index': current_position,
            'current_page_no': current_position,  # legacy, for themes
            'page_labels': page_labels,
            'pages': self.pages,
        }

    def step(self):
        context = self.step_context()
        if not self.on_validation_page:
            self.substvars['current_page_index0'] = context.get('current_page_index0')
            self.substvars['current_page_index'] = context.get('current_page_index')
            self.substvars['current_page_no'] = context.get('current_page_no')
        return template.render(list(self.get_formdef_template_variants(self.steps_templates)), context)

    @classmethod
    def iter_with_block_fields(cls, form, fields):
        from wcs.blocks_widgets import BlockSubWidget

        for field in fields:
            if field.key == 'computed':
                continue
            field_key = '%s' % field.id
            widget = form.get_widget('f%s' % field_key) if form else None
            yield field, field_key, widget, None, None
            if field.key == 'block':
                # we prefill all items
                for idx, subwidget in enumerate(
                    [x for x in widget.widgets if isinstance(x, BlockSubWidget)] if widget else []
                ):
                    if not isinstance(subwidget, BlockSubWidget):
                        continue
                    for subfield in field.block.fields:
                        subfield_key = '%s$%s' % (field.id, subfield.id)
                        subfield_widget = subwidget.get_widget('f%s' % subfield.id) if subwidget else None
                        yield subfield, subfield_key, subfield_widget, field, idx

    @classmethod
    def apply_field_prefills(cls, data, form, displayed_fields, add_button_clicked=False):
        req = get_request()
        had_prefill = False

        if 'prefilling_data' not in data:
            data['prefilling_data'] = {}
        prefilling_new_data = data['prefilling_data']
        prefilling_current_data = copy.copy(prefilling_new_data)

        for field, field_key, widget, block, block_idx in cls.iter_with_block_fields(form, displayed_fields):
            v = None
            prefilled = False
            locked = False

            if block:
                field_key = f'{block.id}${block_idx}${field_key}'

            if field.get_prefill_configuration():
                prefill_user = get_request().user
                if get_request().is_in_backoffice():
                    prefill_user = (
                        get_publisher().substitutions.get_context_variables(mode='lazy').get('form_user')
                    )
                if block:
                    try:
                        row_data = data[block.id]['data'][block_idx]
                    except (TypeError, IndexError, KeyError):
                        row_data = {}
                    with block.block.evaluation_context(row_data, block_idx):
                        v, locked = field.get_prefill_value(user=prefill_user, force_string=True)
                else:
                    v, locked = field.get_prefill_value(
                        user=prefill_user, force_string=bool(field.key != 'block')
                    )

                # always set additional attributes as they will be used for
                # "live prefill", regardless of existing data.
                widget.prefill_attributes = field.get_prefill_attributes()

            if widget and locked:
                widget.prefilled = True  # always set, for "live prefill"
                widget.readonly = 'readonly'
                widget.attrs['readonly'] = 'readonly'

            if add_button_clicked:
                if not block:
                    # do not replay filling fields that are not part of a block
                    continue
                if widget and widget.value:
                    # do not alter subwidget values that may not yet have been
                    # "commited" to data when an "add row" button is clicked
                    continue

            should_prefill = bool(field.get_prefill_configuration())

            has_current_value = False
            if block:
                try:
                    current_value = data[block.id]['data'][block_idx][field.id]
                    has_current_value = True
                except (IndexError, KeyError, TypeError, ValueError):
                    pass
            else:
                try:
                    current_value = data[field_key]
                    has_current_value = True
                except KeyError:
                    pass

            if has_current_value:
                # existing value, update it with the new computed value
                # if it's the same that was previously computed.
                prefill_value = v
                v = current_value
                verify_value = prefilling_current_data.get(field_key)
                if field.convert_value_from_anything:
                    try:
                        verify_value = field.convert_value_from_anything(verify_value)
                    except ValueError:
                        verify_value = None
                if verify_value == current_value:
                    # replace value with new value computed for prefill
                    v = prefill_value
                else:
                    should_prefill = False

            if should_prefill:
                if (
                    get_request().is_in_backoffice()
                    and field.get_prefill_configuration().get('type') == 'geoloc'
                ):
                    # turn off prefilling from geolocation attributes if
                    # the form is filled from the backoffice
                    v = None
                else:
                    if v:
                        prefilled = True
                    # always mark widget as prefilled, even for empty content,
                    # this will add a widget-prefilled CSS class that will be
                    # used for live prefill changes.
                    widget.prefilled = True

            if not prefilled and widget:
                widget.clear_error()
                widget._parsed = False
                if block or field.key == 'block':
                    # keep block outer & inner widgets as _parsed to avoid
                    # later display of "required value" message that should
                    # only happen when pages are submitted.
                    widget._parsed = True

            if v is not None:
                # store computed value, it will be used to compare with
                # submitted value if page is visited again.
                if should_prefill:
                    prefilling_new_data[field_key] = v
                if not isinstance(v, str) and field.convert_value_to_str:
                    v = field.convert_value_to_str(v)
                widget.set_value(v)
                widget.transfer_form_value(req)

                if block:
                    # reset parent block if subwidget has changed; this
                    # prevents "required field" to be displayed on fields that
                    # have just been prefilled.
                    form.get_widget('f%s' % block.id).unparse()
                    form.get_widget('f%s' % block.id).clear_error()

                had_prefill = True
        return had_prefill

    def set_page_title(self):
        step_context = self.step_context()
        if len(step_context.get('page_labels')) > 1:
            get_response().set_title(
                title=get_publisher().translate(self.formdef.name),
                page_title='%s - %s/%s - %s'
                % (
                    get_publisher().translate(self.formdef.name),
                    step_context['current_page_index'],
                    len(step_context['page_labels']),
                    step_context['page_labels'][step_context['current_page_index0']],
                ),
            )
        else:
            get_response().set_title(
                title=get_publisher().translate(self.formdef.name),
                page_title='%s - %s'
                % (
                    get_publisher().translate(self.formdef.name),
                    step_context['page_labels'][step_context['current_page_index0']],
                ),
            )

    def get_honeypot_value(self):
        # simply an hash of the formdef slug
        return hashlib.sha1(self.formdef.slug.encode()).hexdigest()

    def page(
        self,
        page,
        arrival=False,
        page_change=True,
        page_error_messages=None,
        submit_button=None,
        transient_formdata=None,
        page_no=None,
    ):
        displayed_fields = []
        self.current_page = page

        session = get_session()

        magictoken = get_request().form.get('magictoken')
        if page and self.pages.index(page) > 0:
            self.feed_current_data(magictoken)

        has_new_magictoken = False
        if magictoken:
            form_data = session.get_by_magictoken(magictoken, {})
        else:
            form_data = {}

        if page == self.pages[0] and 'magictoken' not in get_request().form:
            magictoken = randbytes(8)
            has_new_magictoken = True

        computed_fields_on_page = list(self.formdef.get_computed_fields_from_page(page))
        computed_data = self.handle_computed_fields(magictoken, computed_fields_on_page)
        if computed_data:
            form_data.update(computed_data)
            self.feed_current_data(magictoken)

        try:
            with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
                form = self.create_form(page, displayed_fields, transient_formdata=transient_formdata)
                if page_change is False and page_error_messages:
                    # ignore form token when there are other errors
                    form._names.pop('_form_id', None)
        except MissingBlockFieldError as e:
            logged_error = get_publisher().record_error(
                str(e), exception=e, notify=True, formdef=self.formdef
            )
            raise errors.InternalServerError(logged_error)

        if submit_button is True:
            # submit_button at True means a non-submitting button has been
            # clicked; details in [ADD_ROW_BUTTON].
            form.clear_errors()
        if page_error_messages:
            form.add_global_errors(page_error_messages)
        if getattr(session, 'ajax_form_token', None):
            form.add_hidden('_ajax_form_token', session.ajax_form_token)
        if get_request().is_in_backoffice():
            form.attrs['data-is-backoffice'] = 'true'
        form.action = self.action_url
        # include a data-has-draft attribute on the <form> element when a draft
        # already exists for the form; this will activate the autosave.
        if not has_new_magictoken and self.has_draft_support():
            form.attrs['data-has-draft'] = 'yes'

        form.add_hidden('magictoken', magictoken)
        data = session.get_by_magictoken(magictoken, {})

        if page == self.pages[0]:
            # when edit_mode's urls are set we come from wfedit()
            return_url = cancelurl = None
            if self.edit_mode and self.edit_mode_return_url:
                return_url = self.edit_mode_return_url
            if 'cancelurl' in get_request().form:
                cancelurl = get_request().form['cancelurl']
            elif self.edit_mode and self.edit_mode_cancel_url != '.':
                cancelurl = self.edit_mode_cancel_url
            if return_url:
                if not get_publisher().is_relatable_url(return_url):
                    raise RequestError(_('Invalid return URL.'))
                form_data['__return_url'] = return_url
            if cancelurl:
                if not get_publisher().is_relatable_url(cancelurl):
                    raise RequestError(_('Invalid cancel URL.'))
                form_data['__cancelurl'] = cancelurl
            if return_url or cancelurl:
                session.add_magictoken(magictoken, form_data)

        if self.edit_mode and (page is None or page == self.pages[-1]):
            form.add_submit('submit', self.edit_mode_submit_label, css_class='form-save-changes')
        elif not self.has_confirmation_page() and (page is None or page == self.pages[-1]):
            form.add_submit(
                'submit', _('Submit'), css_class='form-submit', attrs={'aria-label': _('Submit form')}
            )
        else:
            form.add_submit(
                'submit', _('Next'), css_class='form-next', attrs={'aria-label': _('Go to next page')}
            )

        previous_button_attrs = {'aria-label': _('Go back to previous page')}
        if self.pages.index(page) == 0:
            previous_button_attrs['hidden'] = 'true'
            previous_button_attrs['disabled'] = 'true'

        form.add_submit('previous', _('Previous'), css_class='form-previous', attrs=previous_button_attrs)

        had_prefill = False
        if page_change or submit_button is True:
            # on page change (or when a "add row" button is clicked), we
            # fake a GET request so the form is not altered with errors
            # from the previous submit; if the page was already
            # visited, we restore values; otherwise we set req.form as empty.
            req = get_request()
            req.environ['REQUEST_METHOD'] = 'GET'

            had_prefill = self.apply_field_prefills(
                data, form, displayed_fields, add_button_clicked=bool(submit_button is True)
            )

            if submit_button is True:
                # keep submitted data so it's possible to known the add button
                # was clicked later on.
                req.orig_form = req.form

            if had_prefill:
                # include prefilled data
                transient_formdata = self.get_transient_formdata(magictoken)
                transient_formdata.data.update(self.formdef.get_data(form))
                if self.has_draft_support() and (not arrival or computed_fields_on_page):
                    # save to get prefilling data in database
                    self.save_draft(form_data, page_no=page_no)
                    # and make sure draft formdata id is tracked in session
                    session.add_magictoken(magictoken, form_data)
            else:
                req.form = {}

        else:
            # not a page change, reset_locked_data() will have been called
            # earlier, we use that to set appropriate fields as readonly.
            for field, field_key, widget, block, block_idx in self.iter_with_block_fields(
                form, displayed_fields
            ):
                post_key = 'f%s' % field_key
                if block:
                    post_key = 'f%s$element%s$f%s' % (block.id, block_idx, field.id)
                if get_request().form.get(f'__locked_{post_key}'):
                    widget.readonly = 'readonly'
                    widget.attrs['readonly'] = 'readonly'
                    widget.prefilled = True

        for field, field_key, widget, dummy, dummy in self.iter_with_block_fields(form, displayed_fields):
            if field.get_prefill_configuration():
                # always set additional attributes as they will be used for
                # "live prefill", regardless of existing data.
                widget.prefill_attributes = field.get_prefill_attributes()

        self.formdef.set_live_condition_sources(form, displayed_fields)

        self.is_popup = form._names.get('_popup')

        if had_prefill:
            # pass over prefilled fields that are used as live source of item
            # fields, update matching list of options of matching fields,
            # and mark fields as invalid if the selected value is not available.
            fields_to_update = set()
            for field in computed_fields_on_page:
                if getattr(field, 'live_condition_source', False):
                    fields_to_update.update(field.live_condition_fields)
            for field, field_key, widget, dummy, dummy in self.iter_with_block_fields(form, displayed_fields):
                if getattr(widget, 'prefilled', False) and getattr(widget, 'live_condition_source', False):
                    fields_to_update.update(widget.live_condition_fields)
                elif field.key in ('item', 'items'):
                    kwargs = {}
                    with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
                        if field in fields_to_update:
                            field.perform_more_widget_changes(form, kwargs)
                            if 'options' in kwargs:
                                widget.options = kwargs['options']
                                widget.options_with_attributes = kwargs.get('options_with_attributes')
                        if field.key == 'item':
                            # check selected item field value against updated list of options
                            widget._parse(req)

        self.set_page_title()

        form.add_hidden('step', '0')
        form.add_hidden('page', self.pages.index(page))
        if page:
            form.add_hidden('page_id', page.id)

        if not self.is_popup:
            cancel_label = _('Cancel')
            aria_label = _('Cancel form')
            css_class = 'cancel'
            if self.has_draft_support() and not (data and data.get('is_recalled_draft')):
                cancel_label = _('Discard')
                aria_label = _('Discard form')
                css_class = 'cancel form-discard'
            form.add_submit('cancel', cancel_label, css_class=css_class, attrs={'aria-label': aria_label})

        # add fake fields as honey pot
        honeypot = form.add(
            StringWidget, 'f00', value='', title=_('leave this field blank to prove your humanity'), size=25
        )
        honeypot.is_hidden = True
        if 'level2' in get_publisher().get_site_option('honeypots'):
            honeypot2 = form.add(
                StringWidget,
                'f002',
                value='',
                title=_('and leave this field as prefilled by javascript'),
                size=25,
            )
            honeypot2.is_hidden = True
            form.attrs['data-honey-pot-value'] = self.get_honeypot_value()

        debug_computed_data = []
        debug_http_requests = []
        if get_publisher().get_backoffice_root().is_global_accessible(self.formdef_class.backoffice_section):
            # add comment to help debugging computed data
            for field in computed_fields_on_page:
                if field.id in (computed_data or {}):
                    debug_computed_data.append({'field': field, 'value': computed_data[field.id]})
            debug_http_requests = get_publisher().logged_http_requests

        context = {
            'view': self,
            'page_no': self.get_current_page_no,
            'formdef': LazyFormDef(self.formdef),
            'form_side': self.form_side(data=data, magictoken=magictoken),
            'steps': self.step,
            'html_form': form,
            'debug_computed_data': debug_computed_data,
            'debug_http_requests': debug_http_requests,
            # legacy, used in some themes
            'tracking_code_box': lambda: self.tracking_code_box(data, magictoken),
        }
        self.modify_filling_context(context, page, data)

        if self.is_popup:
            return template.QommonTemplateResponse(
                templates=list(self.get_formdef_template_variants(self.popup_filling_templates)),
                context=context,
                is_django_native=True,
            )

        # legacy
        context['form'] = form

        return template.QommonTemplateResponse(
            templates=list(self.get_formdef_template_variants(self.filling_templates)),
            context=context,
            is_django_native=True,
        )

    def tracking_code_box(self, data, magictoken):
        # legacy, used by some themes
        if not (self.has_draft_support() and data):
            return ''
        context = {
            'view': self,
            'get_tracking_code': lambda: self.get_tracking_code(data, magictoken),
            'is_recalled_draft': bool(data and data.get('is_recalled_draft')),
            'magictoken': magictoken,
        }
        return render_block_to_string(
            list(self.get_formdef_template_variants(self.sidebox_templates)), 'tracking-code-box', context
        )

    def handle_computed_fields(self, magictoken, fields):
        fields = [x for x in fields if x.key == 'computed' and x.value_template]
        computed_values = get_session().get_by_magictoken('%s-computed' % magictoken, {})
        if not fields:
            return computed_values
        if not computed_values:
            get_session().add_magictoken('%s-computed' % magictoken, computed_values)

        # create a temporary map using form variable names, to be used as context
        # variables during evaluation (via temporary_feed below), so we can have
        # computed fields depending on previously computed fields from the same page.
        from wcs.variables import LazyFieldVarComputed

        mapped_computed_values = {}
        for field in fields:
            if field.id in computed_values:
                mapped_computed_values['form_var_%s' % field.varname] = LazyFieldVarComputed(
                    {str(field.id): computed_values[field.id]}, field=field
                )

        with get_publisher().substitutions.temporary_feed(mapped_computed_values, force_mode='lazy'):
            for field in fields:
                if field.freeze_on_initial_value and field.id in computed_values:
                    continue

                with get_publisher().complex_data():
                    try:
                        value = WorkflowStatusItem.compute(
                            field.value_template, raises=True, allow_complex=True
                        )
                    except TemplateError:
                        continue
                    else:
                        value = get_publisher().get_cached_complex_data(value)

                    if isinstance(value, str) and len(value) > 100000:
                        get_publisher().record_error(
                            _('Value too long for field %(field)s: %(value)s (truncated)')
                            % {'field': field.varname, 'value': value[:200]}
                        )
                        value = None

                    if value:
                        try:
                            misc.JSONEncoder(allow_files=False).encode(value)
                        except TypeError:
                            get_publisher().record_error(
                                _('Invalid value "%(value)r" for computed field "%(field)s"')
                                % {'value': value, 'field': field.varname},
                            )
                            value = None

                    if (
                        value
                        and field.data_source
                        and field.data_source.get('type')
                        and field.data_source['type'].startswith('carddef:')
                    ):
                        parts = field.data_source['type'].split(':')
                        try:
                            carddef = CardDef.get_by_urlname(parts[1])
                        except KeyError:
                            get_publisher().record_error(
                                _('Invalid data source for field "%s"') % field.varname,
                            )
                            carddef = None
                            value = None
                        if carddef and not carddef.id_template:
                            try:
                                int(str(value))
                            except (TypeError, ValueError):
                                get_publisher().record_error(
                                    _('Invalid value "%(value)s" for field "%(field)s"')
                                    % {'value': value, 'field': field.varname},
                                )
                                value = None
                    computed_values[field.id] = value
                    mapped_computed_values['form_var_%s' % field.varname] = LazyFieldVarComputed(
                        {str(field.id): computed_values[field.id]}, field=field
                    )
                    get_publisher().substitutions.invalidate_cache()

        return computed_values

    def modify_filling_context(self, context, page, data):
        pass

    def form_side(self, data=None, magictoken=None):
        """Create the elements that typically appear aside the main form
        (tracking code and steps)."""

        context = {
            'view': self,
            'data': data,
            'get_tracking_code': lambda: self.get_tracking_code(data, magictoken),
            'step': self.step,
            'is_recalled_draft': bool(data and data.get('is_recalled_draft')),
            'magictoken': magictoken,
        }

        return template.render(list(self.get_formdef_template_variants(self.sidebox_templates)), context)

    def get_tracking_code(self, data, magictoken):
        if self.formdef.enable_tracking_codes:
            draft_formdata_id = data.get('draft_formdata_id')
            if draft_formdata_id:
                try:
                    formdata = self.formdef.data_class().get(draft_formdata_id)
                    return formdata.tracking_code
                except KeyError:
                    pass
            else:
                return data.get('future_tracking_code')

    def get_transient_formdata(self, magictoken=Ellipsis):
        if magictoken is Ellipsis:
            magictoken = get_request().form.get('magictoken')

        session_data = get_session().get_by_magictoken(magictoken, {})
        draft_formdata = None
        if session_data.get('is_recalled_draft'):
            # restore submission context, this is required to get access to form_parent_* variables
            draft_formdata_id = session_data.get('draft_formdata_id')
            try:
                draft_formdata = self.formdef.data_class().get(draft_formdata_id)
            except KeyError:  # it may not exist
                pass

        formdata = FormData()
        formdata._draft_id = session_data.get('draft_formdata_id')
        if get_request().is_in_backoffice() and not self.edit_mode:
            formdata.user_id = None
            if draft_formdata:
                formdata = draft_formdata  # reuse existing fomdata
        else:
            # create a fake FormData with current submission data
            formdata.user = get_request().user
        formdata._formdef = self.formdef
        if draft_formdata:
            if draft_formdata.submission_context:
                # restore submission context, this is required to get access to form_parent_* variables
                formdata.submission_context = draft_formdata.submission_context
            if draft_formdata.workflow_data:
                # restore workflow_data, this is used for partial edit
                formdata.workflow_data = draft_formdata.workflow_data
        formdata.data = session_data
        formdata.prefilling_data = formdata.data.get('prefilling_data', {})
        computed_values = get_session().get_by_magictoken('%s-computed' % magictoken) or {}
        formdata.data.update(computed_values)

        if formdata.data.get('edited_formdata_id'):
            # during editing (edited_formdata_id is set when starting edition,
            # when there's no magictoken yet)
            self.edited_data = self.formdef.data_class().get(formdata.data.get('edited_formdata_id'))
            self.edit_mode = True  # for live calls made during editing
        if formdata.data.get('edited_testdef_id'):
            from wcs.testdef import TestDef

            testdef = TestDef.get(formdata.data['edited_testdef_id'])
            self.edited_data = testdef.build_formdata(self.formdef, include_fields=True)

        if self.edit_mode:
            if not getattr(self, 'edited_data', None):
                # should not happen, something messed up in user session (?).
                raise RequestError(_('Missing edit data.'))
            if magictoken is None:
                # restore edited data early on as it may be required to
                # create lists with appropriate values on first page.
                formdata.data = self.edited_data.data
            # keep track of original formdata id so it can be used by
            # |exclude_self filter.
            formdata._edited_id = self.edited_data.id
            # keep some attributes from the edited formdata:
            # * user as it may be required as a parameter in data source URLs.
            # * workflow data as it may be used in conditions
            # * submission_context to get form_parent_...
            for attr in (
                'uuid',
                'user',
                'workflow_data',
                'status',
                'submission_context',
                'submission_channel',
                'submission_agent_id',
            ):
                setattr(formdata, attr, getattr(self.edited_data, attr))
            # add previous status as a private attribute as it cannot be computed
            # from history when editing.
            formdata._previous_status = LazyFormData(self.edited_data).previous_status
            return formdata

        formdata.status = ''
        return formdata

    def feed_current_data(self, magictoken):
        formdata = self.get_transient_formdata(magictoken)
        get_publisher().substitutions.feed(formdata)

    def check_disabled(self):
        if self.formdef.is_disabled():
            if self.formdef.disabled_redirection:
                return misc.get_variadic_url(
                    self.formdef.disabled_redirection,
                    get_publisher().substitutions.get_context_variables(mode='lazy'),
                )
            raise errors.AccessForbiddenError()
        return False

    def create_form(self, *args, **kwargs):
        form = self.formdef.create_form(*args, **kwargs)
        if (
            len([x for x in self.formdef.fields if isinstance(x, PageField)]) < 2
            and not self.formdef.confirmation
        ):
            # if there's a form with a single page (at all, not as the result of conditions),
            # and no confirmation page, add native quixote CSRF protection.
            form.add(FormTokenWidget, form.TOKEN_NAME)
        form.add_hidden('previous-page-id', '')
        form.attrs['data-js-features'] = 'true'
        form.attrs['data-live-url'] = self.formdef.get_url(language=get_publisher().current_language) + 'live'
        form.attrs['data-live-validation-url'] = (
            self.formdef.get_url(language=get_publisher().current_language) + 'live-validation'
        )
        form.widgets.append(
            HtmlWidget(
                '''<template id="form_error_tpl">
          <div id="form_error_fieldname" role="alert" class="error"></div>
          </template>'''
            )
        )
        return form

    def create_view_form(self, *args, **kwargs):
        form = self.formdef.create_view_form(*args, **kwargs)
        form.add_hidden('previous-page-id', '')
        return form

    def check_authentication_context(self):
        if not self.formdef.required_authentication_contexts:
            return
        if get_session().get_authentication_context() in self.formdef.required_authentication_contexts:
            return

        get_response().set_title(get_publisher().translate(self.formdef.name))
        r = TemplateIO(html=True)
        r += self.form_side()
        auth_contexts = get_publisher().get_supported_authentication_contexts()
        r += htmltext('<div class="errornotice" role="status" id="stronger-auth-message">')
        r += htmltext('<p>%s</p>') % _('You need a stronger authentication level to fill this form.')
        r += htmltext('</div>')
        root_url = get_publisher().get_root_url()
        for auth_context in self.formdef.required_authentication_contexts:
            r += htmltext('<p><a class="button" href="%slogin/?forceAuthn=true&next=%s">%s</a></p>') % (
                root_url,
                urllib.parse.quote(get_request().get_path_query()),
                _('Login with %s') % auth_contexts[auth_context],
            )
        return r.getvalue()

    def check_unique_submission(self, formdata=None):
        if self.edit_mode:
            return None
        if not self.formdef.only_allow_one:
            return None
        user = get_session().get_user()
        if not user:
            return None
        criterias = [Equal('user_id', str(user.id)), NotEqual('status', 'draft')]
        if formdata and formdata.id:
            criterias.append(NotEqual('id', formdata.id))
        user_forms = self.formdef.data_class().select(criterias, limit=1)
        return user_forms[0].id if user_forms else None

    _pages = None

    @property
    def pages(self):
        if self._pages:
            return self._pages
        transient_formdata = self.get_transient_formdata()
        current_data = transient_formdata.data

        pages = [x for x in self.formdef.fields if x.key == 'page']
        has_page_fields = bool(pages)

        with get_publisher().substitutions.freeze():
            # don't let evaluation of pages alter substitution variables (this
            # avoids a ConditionVars being added with current form data and
            # influencing later code evaluating field visibility based on
            # submitted data) (#27247).
            hidden_pages = [x for x in pages if not x.is_visible(current_data, self.formdef)]

        edit_action = None
        if self.edit_mode and self.edit_action:
            edit_action = self.edit_action
        elif (
            not self.edit_mode
            and transient_formdata.workflow_data
            and '_create_formdata_draft_edit' in transient_formdata.workflow_data
        ):
            edit_action = EditableWorkflowStatusItem()
            edit_action.operation_mode = transient_formdata.workflow_data['_create_formdata_draft_edit'][
                'operation_mode'
            ]
            edit_action.page_identifier = transient_formdata.workflow_data['_create_formdata_draft_edit'][
                'page_identifier'
            ]

        if edit_action and edit_action.operation_mode in ('single', 'partial'):
            edit_pages = edit_action.get_edit_pages(pages)
            edit_pages = [x for x in edit_pages if x not in hidden_pages]
            if not edit_pages:
                raise errors.TraversalError()
            pages = edit_pages
        else:
            pages = [x for x in pages if x not in hidden_pages]
        if not has_page_fields:  # form without page fields
            pages = [None]
        self._pages = pages
        return pages

    def reset_pages_cache(self):
        self._pages = None

    def _q_index(self):
        # noqa pylint: disable=too-many-boolean-expressions
        if (
            self.ensure_parent_category_in_url
            and get_request().get_method() == 'GET'
            and self.formdef.category_id
            and not self.parent_category
            and not self.edit_mode
            and not self.preview_mode
        ) or (self.parent_category and self.formdef.category_id != str(self.parent_category.id)):
            url = self.formdef.get_url(include_category=True)
            if get_request().get_query():
                url += '?' + get_request().get_query()
            return redirect(url)
        user = get_request().user
        if user and user.is_api_user:
            raise errors.AccessForbiddenError(_('Not an API view.'))
        self.check_access()
        authentication_context_check_result = self.check_authentication_context()
        if authentication_context_check_result:
            return authentication_context_check_result

        if not self.edit_mode and self.check_disabled():
            return redirect(self.check_disabled())

        session = get_session()
        if not session.id:
            # force session to be written down, this is required so
            # [session_hash_id] is available on the first page.
            session.force()

        if self.has_draft_support():
            if get_request().form.get('_ajax_form_token'):
                # _ajax_form_token is immediately removed, this prevents
                # late autosave() to overwrite data after the user went to a
                # different page.
                try:
                    session.remove_form_token(get_request().form.get('_ajax_form_token'))
                except ValueError:
                    # already got removed, this may be because the form got
                    # submitted twice.
                    pass
            session.ajax_form_token = session.create_form_token()

        if get_request().form.get('magictoken'):
            no_magic = object()
            session_magic_token = session.get_by_magictoken(get_request().form.get('magictoken'), no_magic)
            if session_magic_token is no_magic:
                if get_request().form.get('page') != '0' or get_request().form.get('step') != '0':
                    # the magictoken that has been submitted is not available
                    # in the session and we're not on the first page of the
                    # first step,
                    if not (get_session().user or get_session().anonymous_formdata_keys):
                        # that means we probably lost the session in mid-air.
                        get_session().add_message(_('Sorry, your session has been lost.'))
                    else:
                        # or that the user went back to a previous page on a submitted form.
                        get_session().add_message(self.already_submitted_message)
                    return redirect(self.formdef.get_submission_url(get_request().is_in_backoffice()))
            self.feed_current_data(get_request().form.get('magictoken'))
        else:
            self.feed_current_data(None)
            if not self.edit_mode and (
                get_request().get_method() == 'GET' and 'mt' not in get_request().form and get_request().user
            ):
                self.initial_drafts = list(
                    LazyFormDef(self.formdef).objects.current_user().drafts().order_by('receipt_time')
                )
            # first hit on first page, if tracking code are enabled and we
            # are not editing an existing formdata, generate a new tracking
            # code.
            if not self.edit_mode and self.formdef.enable_tracking_codes and 'mt' not in get_request().form:
                tracking_code = TrackingCode()
                tracking_code.store()
                token = randbytes(8)
                get_request().form['magictoken'] = token
                session.add_magictoken(token, {'future_tracking_code': tracking_code.id})

        existing_formdata = None
        if self.edit_mode:
            existing_formdata = self.edited_data.data
            request_data = {k: v for k, v in get_request().form.items() if k != '_popup'}
            if not request_data:
                # on the initial visit editing the form (i.e. not after
                # clicking for previous or next page), we need to load the
                # existing data into the session
                self.edited_data.feed_session()
                token = randbytes(8)
                get_request().form['magictoken'] = token
                self.edited_data.data['edited_formdata_id'] = self.edited_data.id
                session.add_magictoken(token, self.edited_data.data)

                # and restore computed data
                computed_values = {}
                for field in self.formdef.fields or []:
                    if field.key != 'computed':
                        continue
                    if field.id in self.edited_data.data:
                        computed_values[field.id] = self.edited_data.data.get(field.id)
                session.add_magictoken('%s-computed' % token, computed_values)

        # redirect to existing formdata if form is configured to only allow one
        # per user and it's already there.
        existing_form_id = self.check_unique_submission()
        if existing_form_id:
            return redirect('%s/' % existing_form_id)

        get_response().add_javascript(['jquery.js', 'qommon.forms.js'])
        form = Form()
        form.add_hidden('step', '-1')
        form.add_hidden('page', '-1')
        form.add_hidden('magictoken', '-1')
        form.add_submit('cancel')

        if self.has_draft_support():
            form.add_submit('removedraft')

        if not form.is_submitted():
            if 'mt' in get_request().form:
                magictoken = get_request().form['mt']
                data = session.get_by_magictoken(magictoken, Ellipsis)
                if data is Ellipsis:
                    return redirect(get_request().get_path())
                computed_values = session.get_by_magictoken('%s-computed' % magictoken, {})
                if not get_request().is_in_backoffice():
                    # don't remove magictoken as the backoffice agent may get
                    # the page reloaded.
                    session.remove_magictoken(magictoken)
                if data or computed_values:
                    # create a new one since the other has been exposed in a url
                    magictoken = randbytes(8)
                    session.add_magictoken(magictoken, data or {})
                    session.add_magictoken('%s-computed' % magictoken, computed_values)

                    get_request().form['magictoken'] = magictoken
                    self.feed_current_data(magictoken)
                    if 'page_no' in data and int(data['page_no']) != 0:
                        page_no = int(data['page_no'])
                        del data['page_no']
                        if page_no == -1 or page_no >= len(self.pages):
                            req = get_request()
                            for k, v in data.items():
                                req.form['f%s' % k] = v
                            for field in self.formdef.fields:
                                if field.id not in data:
                                    continue
                                if field.convert_value_to_str:
                                    req.form['f%s' % field.id] = field.convert_value_to_str(data[field.id])
                            return self.validating(data)
                    else:
                        page_no = 0
                    return self.page(self.pages[page_no], page_change=True)
            self.feed_current_data(None)
            if not self.pages:
                return template.error_page(_('This form has no visible page.'))
            return self.page(self.pages[0], arrival=True, page_no=0)

        if form.get_submit() == 'cancel':
            magictoken = form.get_widget('magictoken').parse()
            if self.edit_mode:
                return redirect(
                    session.get_by_magictoken(magictoken, {}).get('__cancelurl') or self.edit_mode_cancel_url
                )
            if self.has_draft_support():
                current_draft = self.get_current_draft()
                if current_draft:
                    discard_draft = True
                    if magictoken:
                        data = session.get_by_magictoken(magictoken, {})
                        if data.get('is_recalled_draft'):
                            discard_draft = False
                    if discard_draft:
                        current_draft.remove_self()
            try:
                cancelurl = session.get_by_magictoken(magictoken, {}).get('__cancelurl')
                if cancelurl:
                    return redirect(cancelurl)
            except KeyError:
                pass
            return self.cancelled()

        try:
            step = int(form.get_widget('step').parse())
        except (TypeError, ValueError):
            step = 0

        if step == 0:
            try:
                page_no = int(form.get_widget('page').parse())
                page = self.pages[page_no]
            except (TypeError, ValueError, IndexError):
                # this situation shouldn't arise (that likely means the
                # page hidden field had an error in its submission), in
                # that case we just fall back to the first page.
                page_no = 0
                if not self.pages:
                    # some condition led to all pages being conditioned out,
                    # abort with an error.
                    return template.error_page(_('This form has no visible page.'))
                page = self.pages[0]
            try:
                magictoken = form.get_widget('magictoken').parse()
            except KeyError:
                magictoken = randbytes(8)

            self.feed_current_data(magictoken)

            submitted_fields = []
            transient_formdata = self.get_transient_formdata()
            with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
                form = self.create_form(
                    page=page, displayed_fields=submitted_fields, transient_formdata=transient_formdata
                )
            form.add_submit('previous')
            if self.has_draft_support():
                form.add_submit('removedraft')
            form.add_submit('submit')
            if page_no > 0 and form.get_submit() == 'previous':
                return self.previous_page(page_no, magictoken)

            if self.has_draft_support() and form.get_submit() == 'removedraft':
                return self.removedraft()

            form_data = session.get_by_magictoken(magictoken, {})
            with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
                # reset locked data with newly submitted values, this allows
                # for templates referencing fields from the sampe page.
                self.reset_locked_data(form)
                try:
                    data = self.formdef.get_data(form, raise_on_error=True)
                except SetValueError as e:
                    return self.page(
                        page,
                        page_change=False,
                        page_error_messages=[
                            {'summary': _('Technical error, please try again.'), 'details': e}
                        ],
                        transient_formdata=transient_formdata,
                    )
                computed_data = self.handle_computed_fields(magictoken, submitted_fields)

            form_data.update(data)

            for field in submitted_fields:
                if not field.is_visible(form_data, self.formdef) and 'f%s' % field.id in form._names:
                    del form._names['f%s' % field.id]

            page_error_messages = []
            if form.get_submit() == 'submit' and page:
                post_conditions = page.post_conditions or []
                # create a new dictionary to hold live data, this makes sure
                # a new ConditionsVars will get added to the substitution
                # variables.
                form_data = copy.copy(session.get_by_magictoken(magictoken, {}))
                if form_data:
                    # keep new copy in session
                    session.add_magictoken(magictoken, form_data)
                try:
                    with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
                        data = self.formdef.get_data(form, raise_on_error=True)
                except SetValueError as e:
                    return self.page(
                        page,
                        page_change=False,
                        page_error_messages=[
                            {'summary': _('Technical error, please try again.'), 'details': e}
                        ],
                        transient_formdata=transient_formdata,
                    )
                form_data.update(data)
                form_data.update(computed_data)
                for i, post_condition in enumerate(post_conditions):
                    condition = post_condition.get('condition')
                    error_message = post_condition.get('error_message')
                    errored = False
                    try:
                        if not page.evaluate_condition(form_data, self.formdef, condition):
                            errored = True
                    except RuntimeError:
                        errored = True
                    if errored:
                        form.add(HiddenErrorWidget, 'post_condition%d' % i)
                        form.set_error('post_condition%d' % i, 'error')
                        error_message = get_publisher().translate(error_message)
                        error_message = WorkflowStatusItem.compute(error_message, allow_ezt=False)
                        page_error_messages.append(error_message)

            honeypot_error = False
            # 🍯
            if get_request().form.get('f00') or (
                'level2' in get_publisher().get_site_option('honeypots')
                and get_request().form.get('f002') != self.get_honeypot_value()
            ):
                honeypot_error = True
                form.add(HiddenErrorWidget, 'honeypot')
                form.set_error('honeypot', 'error')
                page_error_messages.append(_('Honey pots should be left untouched.'))

            # form.get_submit() returns the name of the clicked button, and
            # it will return True if the form has been submitted, but not
            # by clicking on a submit widget; for example if an "add row"
            # button is clicked. [ADD_ROW_BUTTON]
            if form.has_errors() or form.get_submit() is True:
                token_error = form.get_widget('_form_id') and form.get_widget('_form_id').has_error()
                if self.has_draft_support() and not (honeypot_error or token_error):
                    # save draft during server roundtrip
                    try:
                        self.save_draft(form_data)
                        session.add_magictoken(magictoken, form_data)  # make sure draft id is saved
                    except SubmittedDraftException:
                        get_session().add_message(self.already_submitted_message)
                        return redirect(
                            self.formdef.get_submission_url(backoffice=get_request().is_in_backoffice())
                        )
                    except NothingToUpdate:
                        get_session().add_message(_('Technical error saving draft, please try again.'))
                        return redirect(
                            self.formdef.get_submission_url(backoffice=get_request().is_in_backoffice())
                        )
                return self.page(
                    page,
                    page_change=False,
                    page_error_messages=page_error_messages,
                    submit_button=form.get_submit(),
                    transient_formdata=transient_formdata,
                )

            form_data = session.get_by_magictoken(magictoken, {})
            with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
                try:
                    data = self.formdef.get_data(form, raise_on_error=True)
                except SetValueError as e:
                    return self.page(
                        page,
                        page_change=False,
                        page_error_messages=[
                            {'summary': _('Technical error, please try again.'), 'details': e}
                        ],
                        transient_formdata=transient_formdata,
                    )
            form_data.update(data)
            form_data.update(computed_data)

            session.add_magictoken(magictoken, form_data)

            currently_listed_pages = self.pages
            self.reset_pages_cache()
            new_listed_pages = self.pages
            if currently_listed_pages != new_listed_pages:
                # conditions changed, take first displayed page after current page
                take_next_page = False
                page_no = len(new_listed_pages)  # if no page found display confirmation page
                for page_field in self.formdef.fields:
                    if page_field == page:
                        take_next_page = True
                    elif page_field.key == 'page' and take_next_page and page_field in new_listed_pages:
                        page_no = new_listed_pages.index(page_field)
                        break
            else:
                page_no += 1

            draft_id = session.get_by_magictoken(magictoken, {}).get('draft_formdata_id')
            if draft_id:
                # if there's a draft (be it because drafts are enabled or
                # because the formdata was created as a draft via the
                # submission API), update it with current data.
                try:
                    self.autosave_draft(draft_id, page_no, form_data)
                except SubmittedDraftException:
                    if get_request().is_in_backoffice():
                        get_session().add_message(self.already_submitted_message)
                        return redirect(get_publisher().get_backoffice_url() + '/submission/')
                    return template.error_page(self.already_submitted_message)
            elif self.has_draft_support():
                # if there's no draft yet and drafts are supported, create one
                self.save_draft(form_data, page_no)

            # the page has been successfully submitted, maybe new pages
            # should be revealed.
            self.clean_submission_context()
            self.feed_current_data(magictoken)
            self.reset_pages_cache()

            if int(page_no) == len(self.pages):
                # last page has been submitted
                req = get_request()
                for field in self.formdef.fields:
                    k = field.id
                    if k in form_data:
                        v = form_data[k]
                        if field.convert_value_to_str:
                            v = field.convert_value_to_str(v)
                        req.form['f%s' % k] = v
                if self.edit_mode:
                    view_form = self.create_view_form(form_data, use_tokens=False)
                    try:
                        return self.submitted_existing(view_form)
                    except SetValueError as e:
                        return self.page(
                            page,
                            page_change=False,
                            page_error_messages=[
                                {'summary': _('Technical error, please try again.'), 'details': e}
                            ],
                            transient_formdata=transient_formdata,
                        )
                if self.has_confirmation_page():
                    return self.validating(form_data)

                step = 1  # so it will flow to submit
                # kind of restore state
                form = Form()
                form.add_hidden('step', '-1')
                form.add_hidden('page', '-1')
                form.add_hidden('magictoken', '-1')
                form.add_submit('cancel')
                if self.has_draft_support():
                    form.add_submit('removedraft')

            else:
                return self.page(self.pages[page_no])

        self.reset_locked_data(form)
        if step == 1:
            form.add_submit('previous')
            if form.get_submit() == 'previous':
                return self.previous_page(len(self.pages), magictoken)
            step = 2  # so it will flow to submit

        if step == 2:
            if 'previous' not in form:
                form.add_submit('previous')
            magictoken = form.get_widget('magictoken').parse()
            self.feed_current_data(magictoken)
            form_data = session.get_by_magictoken(magictoken, {})

            if form.get_submit() == 'previous':
                return self.previous_page(len(self.pages), magictoken)

            if self.has_draft_support() and form.get_submit() == 'removedraft':
                return self.removedraft()

            # so it gets FakeFileWidget in preview mode
            form = self.create_view_form(form_data, use_tokens=self.has_confirmation_page())
            if self.formdef.has_captcha_enabled() and not (
                get_session().get_user() or get_session().won_captcha
            ):
                form.add_captcha(hint='')
                if form.captcha.has_error():
                    return self.validating(form_data)

            if form.has_errors():
                form_token_widget = form.get_widget(form.TOKEN_NAME)
                if form_token_widget and form_token_widget.has_error():
                    # Token error if the form is submitted a second time
                    return template.error_page(_('This form has already been submitted.'))
                # Something else, typically this means a draft has been loaded and
                # the field checks are no longer ok (for example a check on "date must be
                # after today"). Push back user to the first page to correct the errors
                get_session().add_message(_('Unexpected field error, please check.'))
                return self.page(self.pages[0])

            try:
                return self.submitted(form, existing_formdata)
            except (SetValueError, RequiredUserException) as e:
                page_error_messages = []
                if isinstance(e, SetValueError):
                    page_error_messages = [{'summary': _('Technical error, please try again.'), 'details': e}]

                if get_request().form.get('step') == '2':
                    # submit came from the validation page
                    return self.validating(form_data, page_error_messages=page_error_messages)

                # last page
                return self.page(
                    page,
                    page_change=False,
                    page_error_messages=page_error_messages,
                    transient_formdata=transient_formdata,
                )

    def reset_locked_data(self, form):
        # reset locked fields, making sure the user cannot alter them.
        prefill_user = get_request().user
        if get_request().is_in_backoffice():
            prefill_user = get_publisher().substitutions.get_context_variables(mode='lazy').get('form_user')
        for field, field_key, widget, block, block_idx in self.iter_with_block_fields(
            form, self.formdef.fields
        ):
            if not field.get_prefill_configuration():
                continue
            post_key = 'f%s' % field_key
            if block:
                post_key = 'f%s$element%s$f%s' % (block.id, block_idx, field.id)
            if post_key not in get_request().form and field.key != 'bool':
                # always handle bool fields as an unchecked box won't appear
                # in get_request().form
                continue
            if block:
                try:
                    block_data = (
                        get_publisher()
                        .substitutions.get_context_variables(mode='lazy')['form']
                        ._formdata.data.get(block.id)
                    )
                    row_data = block_data['data'][block_idx]
                except (AttributeError, TypeError, IndexError, KeyError):
                    row_data = {}
                with block.block.evaluation_context(row_data, block_idx):
                    v, locked = field.get_prefill_value(user=prefill_user)
            else:
                v, locked = field.get_prefill_value(
                    user=prefill_user, force_string=bool(field.key != 'block')
                )
            if locked:
                if not isinstance(v, str) and field.convert_value_to_str:
                    # convert structured data to strings as if they were
                    # submitted by the browser.
                    v = field.convert_value_to_str(v)
                get_request().form[post_key] = v
                if widget:
                    widget.set_value(v)
                    if block:
                        # child widget value was changed, mark parent widgets
                        # as unparsed
                        block_widget = form.get_widget('f%s' % block.id)
                        block_widget._parsed = False
                        block_widget.widgets[0]._parsed = False

                # keep track of locked field, this will be used when
                # redisplaying the same page in case of errors.
                get_request().form[f'__locked_{post_key}'] = True

    def previous_page(self, page_no, magictoken):
        try:
            previous_page_id = get_request().form.get('previous-page-id')
            if previous_page_id:
                new_page_no, previous_page = [
                    x for x in enumerate(self.pages[:page_no]) if x[1].id == previous_page_id
                ][0]
            else:
                new_page_no = page_no - 1
                previous_page = self.pages[new_page_no]
        except IndexError:
            new_page_no = 0
            previous_page = self.pages[0]

        form_data = get_session().get_by_magictoken(magictoken, {})
        draft_id = form_data.get('draft_formdata_id')
        if draft_id:
            # save draft to have new page number
            try:
                self.autosave_draft(draft_id, new_page_no, form_data)
            except SubmittedDraftException:
                get_session().add_message(self.already_submitted_message)
                return redirect(self.formdef.get_submission_url(backoffice=get_request().is_in_backoffice()))
        return self.page(previous_page, page_change=True)

    def get_page_id(self, page_no):
        if page_no < len(self.pages):
            if self.pages == [None]:
                # form without pages
                return '_first_page'
            return self.pages[page_no].id
        # After subtmitting the last standard page,
        # page_no is out of range regarding pages.
        # If the form has a confirmation page a draft for that page is stored.
        # If not that draft will be immediately converted to a standard formdata.
        if self.has_confirmation_page():
            return '_confirmation_page'
        return None

    def removedraft(self):
        magictoken = get_request().form.get('magictoken')
        if magictoken:
            form_data = get_session().get_by_magictoken(magictoken, {})
            if form_data.get('draft_formdata_id'):
                self.formdef.data_class().remove_object(form_data.get('draft_formdata_id'))
        return redirect(get_publisher().get_root_url())

    def autosave_draft(self, draft_id, page_no, form_data, where=None):
        try:
            formdata = self.formdef.data_class().get(draft_id)
        except KeyError:
            return

        if not formdata.status == 'draft':
            raise SubmittedDraftException()

        formdata.page_no = page_no
        formdata.page_id = self.get_page_id(page_no)
        formdata.data = form_data
        formdata.receipt_time = localtime()
        if not get_request().is_in_backoffice():
            formdata.user = get_request().user
        formdata.store()

    AUTOSAVE_TIMEOUT = 0.2

    def autosave(self):
        get_response().set_content_type('application/json')
        get_request().ignore_session = True

        def result_error(reason):
            return json.dumps({'result': 'error', 'reason': reason})

        ajax_form_token = get_request().form.get('_ajax_form_token')
        if not ajax_form_token:
            return result_error('no ajax form token')
        if not get_session().has_form_token(ajax_form_token):
            return result_error('obsolete ajax form token')

        try:
            page_no = int(get_request().form.get('page'))
        except TypeError:
            return result_error('missing page_no')
        except ValueError:
            return result_error('bad page_no')

        magictoken = get_request().form.get('magictoken')
        if not magictoken:
            return result_error('missing magictoken')

        session = get_session()
        if not session:
            return result_error('missing session')

        self.feed_current_data(magictoken)

        form_data = session.get_by_magictoken(magictoken, {})
        if not form_data:
            return result_error('missing data')

        try:
            page = self.pages[page_no]
        except IndexError:
            # XXX: this should not happen but if pages use conditionals based
            # on webservice results, there can be (temporary?) inconsistencies.
            return result_error('ouf ot range page_no')
        form = self.create_form(page=page)
        try:
            data = self.formdef.get_data(form, raise_on_error=True)
        except SetValueError as e:
            return result_error('form deserialization failed: %s' % e)
        if not data:
            return result_error('nothing to save')

        form_data.update(data)

        # reload session to make sure _ajax_form_token is still valid
        session = get_session_manager().get(get_session().id)
        if not session:
            return result_error('cannot get ajax form token (lost session)')
        if not session.has_form_token(get_request().form.get('_ajax_form_token')):
            return result_error('obsolete ajax form token (late check)')

        if time.time() - get_request().t0 > self.AUTOSAVE_TIMEOUT:
            return result_error('too long')

        try:
            self.save_draft(form_data, page_no, where=[Equal('page_no', str(page_no))])
        except NothingToUpdate:
            return result_error('no valid form to update')
        except SubmittedDraftException:
            return result_error('form has already been submitted')

        return json.dumps({'result': 'success'})

    def save_draft(self, data, page_no=None, where=None):
        filled = self.get_current_draft() or self.formdef.data_class()()
        new_draft = bool(filled.id is None)
        if filled.id and filled.status != 'draft':
            raise SubmittedDraftException()
        filled.data = data
        filled.prefilling_data = data.get('prefilling_data')
        filled.status = 'draft'
        if page_no is not None:
            filled.page_no = page_no
            filled.page_id = self.get_page_id(page_no)
        filled.receipt_time = localtime()
        where = [Equal('status', 'draft')] + (where or [])
        if get_request().is_in_backoffice():
            # if submitting via backoffice store fhe formdata as is.
            filled.store(where=where)
        else:
            # if submitting via frontoffice, attach current user, eventually
            # anonymous, to the formdata
            filled.user = get_request().user
            filled.store(where=where)

            if not filled.user_id:
                if get_session().mark_anonymous_formdata(filled):
                    get_session().store()
            elif new_draft:
                # keep at most "max_per_user" drafts per user
                data_class = self.formdef.data_class()
                for id in data_class.get_sorted_ids(
                    '-last_update_time', [Equal('status', 'draft'), Equal('user_id', str(filled.user_id))]
                )[self.formdef.get_drafts_max_per_user() :]:
                    data_class.remove_object(id)

        if new_draft:
            data['draft_formdata_id'] = filled.id
            get_session().store()
        self.set_tracking_code(filled, data)

        return filled

    def get_current_draft(self):
        magictoken = get_request().form.get('magictoken')
        if magictoken:
            session = get_session()
            form_data = session.get_by_magictoken(magictoken, {})
            draft_formdata_id = form_data.get('draft_formdata_id')
            if draft_formdata_id:
                # there was a draft, use it.
                try:
                    return self.formdef.data_class().get(draft_formdata_id)
                except KeyError:  # it may not exist
                    pass
        return None

    @record_timings(name='/live call', record_if_over=5)
    def live(self):
        get_request().ignore_session = True
        # live evaluation of fields
        get_response().set_content_type('application/json')

        def result_error(reason):
            return json.dumps({'result': 'error', 'reason': reason})

        session = get_session()
        if not (session and session.id):
            return result_error('missing session')

        page_id = get_request().form.get('page_id')
        if page_id:
            for field in self.formdef.fields:
                if str(field.id) == page_id:
                    page = field
                    break
            else:
                return result_error('unknown page_id')
        else:
            page = None
        add_timing_mark('live get_transient_formdata')
        formdata = self.get_transient_formdata()
        get_publisher().substitutions.feed(formdata)
        displayed_fields = []
        add_timing_mark('live create form')
        with (
            get_publisher().substitutions.temporary_feed(formdata, force_mode='lazy'),
            get_publisher().keep_all_block_rows(),
        ):
            form = self.create_form(page=page, displayed_fields=displayed_fields, transient_formdata=formdata)
        add_timing_mark('live get_data')
        try:
            formdata.data.update(self.formdef.get_data(form, raise_on_error=True))
        except SetValueError as e:
            return result_error('form deserialization failed: %s' % e)
        return FormStatusPage.live_process_fields(form, formdata, displayed_fields)

    def live_validation(self):
        # live validation of field values
        get_request().ignore_session = True
        get_response().set_content_type('application/json')

        def result_error(reason):
            return json.dumps({'err': 2, 'msg': reason})

        session = get_session()
        if not (session and session.id):
            return result_error('missing session')

        field_ref = get_request().form.get('field')
        if not field_ref:
            return result_error('missing ?field parameter')

        parts = field_ref.split('__')
        if len(parts) not in (1, 3):
            return result_error('invalid ?field parameter')
        for field in self.formdef.fields:
            if 'f%s' % field.id == parts[0]:
                break
        else:
            return result_error('unknown field')
        if len(parts) == 3:  # block field
            for subfield in field.block.fields or []:
                if 'f%s' % subfield.id == parts[2]:
                    break
            else:
                return result_error('unknown sub field')
            field = subfield
            field.id = field_ref[1:].replace('__', '$')

        form = Form()
        widget = field.add_to_form(form)
        widget.load_options_to_check_for_errors = False
        error = widget.get_error()
        if error:
            resp = {'err': 1, 'msg': str(error)}
            if hasattr(widget, 'error_code'):
                error_message = ErrorMessage(widget.error_code, '')
                resp['errorType'] = error_message.camel_code()
            return json.dumps(resp)
        return json.dumps({'err': 0})

    def clean_submission_context(self):
        get_publisher().substitutions.unfeed(lambda x: x.__class__.__name__ == 'ConditionVars')
        get_publisher().substitutions.unfeed(lambda x: isinstance(x, FormData))

    def submitted(self, form, existing_formdata=None):
        if existing_formdata:  # modifying
            filled = existing_formdata
            # XXX: what about status?
            filled.data = self.formdef.get_data(form, raise_on_error=True)
        else:
            with sql.atomic() as transaction:
                filled = self.get_current_draft() or self.formdef.data_class()()
                if filled.id and not filled.is_draft():
                    # check double submission
                    get_session().add_message(self.already_submitted_message)
                    return redirect(self.formdef.get_submission_url(get_request().is_in_backoffice()))
                filled.just_created(save_content_snapshot=False)
                if filled.id:
                    # store if formdata already exists, to get it out of draft status,
                    # so the double-submission check above works.
                    filled.store()

                # this is already checked in _q_index but it's done a second time
                # just before a new form is to be stored.
                existing_form_id = self.check_unique_submission(formdata=filled)
                if existing_form_id:
                    transaction.rollback()  # "unsave" formdata
                    return redirect('%s/' % existing_form_id)

                if not filled.submission_context:
                    filled.submission_context = {}
                filled.submission_context['language'] = get_publisher().current_language
                filled.data = self.formdef.get_data(form, raise_on_error=True)
                filled.evolution[0].add_part(ContentSnapshotPart(formdata=filled, old_data={}))

        magictoken = get_request().form['magictoken']
        computed_values = get_session().get_by_magictoken('%s-computed' % magictoken, {})
        filled.data.update(computed_values)
        session = get_session()
        filled.update_workflow_data({'_source_ip': session._remote_address})
        filled.user = get_request().user
        if get_request().get_path().startswith('/backoffice/'):
            filled.user_id = None

        filled.store()
        self.set_tracking_code(filled)
        session.remove_magictoken(get_request().form.get('magictoken'))

        url = None
        if existing_formdata is None:
            self.clean_submission_context()
            filled.refresh_from_storage()
            filled.record_workflow_event('frontoffice-created')
            if get_publisher().has_site_option('perform-workflow-as-job'):
                filled.perform_workflow_as_job()
            else:
                url = filled.perform_workflow()

        if not filled.user_id:
            get_session().mark_anonymous_formdata(filled)

        if not url:
            if get_request().get_path().startswith('/backoffice/'):
                url = filled.get_url(backoffice=True)
            else:
                url = filled.get_url(language=get_publisher().current_language)

        add_clamd_scan_job(filled)

        return redirect(url)

    def cancelled(self):
        return redirect(get_publisher().get_root_url())

    def set_tracking_code(self, formdata, magictoken_data=None):
        if not self.formdef.enable_tracking_codes:
            return
        if formdata.tracking_code:
            return
        code = TrackingCode()
        if magictoken_data and 'future_tracking_code' in magictoken_data:
            code.id = magictoken_data['future_tracking_code']
        code.formdata = formdata  # this will .store() the code

    def submitted_existing(self, form):
        new_data = copy.deepcopy(self.edited_data.data)
        current_form_data = self.formdef.get_data(form, raise_on_error=True, pages=self.pages)
        new_data.update(current_form_data)
        magictoken = get_request().form['magictoken']
        computed_values = get_session().get_by_magictoken('%s-computed' % magictoken, {})
        new_data.update(computed_values)
        old_data = copy.deepcopy(self.edited_data.data)
        self.edited_data.data = new_data
        if getattr(self, 'selected_user_id', None):
            # user selection in backoffice
            self.edited_data.user_id = self.selected_user_id
        if getattr(self, 'testdef', None):
            from wcs.testdef import TestDef

            # discard data from hidden pages
            self.edited_data.data = current_form_data

            testdef = TestDef.create_from_formdata(self.formdef, self.edited_data)

            if hasattr(self, 'edit_form_action'):
                self.edit_form_action.form_data = testdef.data['fields']
                self.testdef.store()
                return redirect(self.testdef.get_admin_url() + 'workflow/')

            self.testdef.data = testdef.data
            self.testdef.expected_error = None
            self.testdef.store(comment=_('Change in test data'))
            return redirect(self.testdef.get_admin_url())

        ContentSnapshotPart.take(formdata=self.edited_data, old_data=old_data, user=get_request().user)
        self.edited_data.store()
        # remove previous vars and formdata from substitution variables
        self.clean_submission_context()
        # and add new one
        get_publisher().substitutions.feed(self.edited_data)
        wf_status = self.edited_data.get_status()
        url = None
        for item in wf_status.items:
            if item.id == self.edit_action.id:
                url = item.finish_edition(self.edited_data, get_request().user)
                break

        if get_request().form.get('_popup'):
            popup_response_data = json.dumps(
                {
                    'value': str(self.edited_data.get_natural_key()),
                    'obj': str(self.edited_data.default_digest),
                    'edit_related_url': self.edited_data.get_edit_related_url() or '',
                    'view_related_url': self.edited_data.get_view_related_url() or '',
                }
            )
            return template.QommonTemplateResponse(
                templates=['wcs/backoffice/popup_response.html'],
                context={'popup_response_data': popup_response_data},
                is_django_native=True,
            )

        if self.edit_mode:
            url = get_session().get_by_magictoken(magictoken, {}).get('__return_url')

        if not url:
            url = self.edited_data.get_url(
                backoffice=get_request().is_in_backoffice(), language=get_publisher().current_language
            )
        return redirect(url)

    def validating(self, data, page_error_messages=None):
        self.on_validation_page = True
        get_request().view_name = 'validation'
        self.set_page_title()
        # fake a GET request to avoid previous page POST data being carried
        # over in rendering.
        get_request().environ['REQUEST_METHOD'] = 'GET'
        form = self.create_view_form(data)
        if page_error_messages:
            form.add_global_errors(page_error_messages)
        token_widget = form.get_widget(form.TOKEN_NAME)
        token_widget._parsed = True
        if self.formdef.has_captcha_enabled() and not (get_session().get_user() or get_session().won_captcha):
            get_request().form['captcha$q'] = ''
            captcha_text = TextsDirectory.get_html_text('captcha-page')
            if captcha_text:
                form.widgets.append(HtmlWidget(captcha_text))
            form.add_captcha(hint='')
            form.captcha.has_error = lambda request: False
        form.add_submit(
            'submit', _('Submit'), css_class='form-submit', attrs={'aria-label': _('Submit form')}
        )
        form.add_submit(
            'previous',
            _('Previous'),
            css_class='form-previous',
            attrs={'aria-label': _('Go back to previous page')},
        )
        cancel_label = _('Cancel')
        css_class = 'cancel'
        aria_label = _('Cancel form')
        if self.has_draft_support() and not (data and data.get('is_recalled_draft')):
            cancel_label = _('Discard')
            css_class = 'cancel form-discard'
            aria_label = _('Discard form')
        form.add_submit('cancel', cancel_label, css_class=css_class, attrs={'aria-label': aria_label})
        form.add_hidden('step', '2')
        magictoken = get_request().form['magictoken']
        form.add_hidden('magictoken', magictoken)

        context = {
            'view': self,
            'html_form': form,
            'form_side': self.form_side(data=data, magictoken=magictoken),
            'steps': self.step,
            # legacy, used in some themes
            'tracking_code_box': lambda: self.tracking_code_box(data, magictoken),
        }
        context['form'] = form  # legacy
        self.modify_validation_context(context, data)

        return template.QommonTemplateResponse(
            templates=list(self.get_formdef_template_variants(self.validation_templates)),
            context=context,
            is_django_native=True,
        )

    def modify_validation_context(self, context, data):
        pass

    def get_url_with_query(self):
        query = get_request().get_query()
        url = self.formdef.get_url()
        if query:
            url += '?' + query
        return url

    def tryauth(self):
        return tryauth(self.get_url_with_query())

    def auth(self):
        return auth(self.get_url_with_query())

    def forceauth(self):
        return forceauth(self.get_url_with_query())

    def qrcode(self):
        img = qrcode.make(self.formdef.get_url())
        s = io.BytesIO()
        img.save(s)
        if get_request().get_query() == 'download':
            get_response().set_header(
                'content-disposition', 'attachment; filename=qrcode-%s.png' % self.formdef.url_name
            )
        get_response().set_content_type('image/png')
        return s.getvalue()

    @classmethod
    def get_status_page_class(cls):
        return PublicFormStatusPage

    def _q_lookup(self, component):
        if self.ensure_parent_category_in_url and not self.parent_category:
            # handle special case where there's a formdef and a category with the same
            # slug; recurse into self with parent_category set to avoid the redirect to
            # full URL step.
            category = Category.get_by_slug(self.formdef.url_name, ignore_errors=True)
            formdef = self.formdef_class.get_by_urlname(component, ignore_errors=True)
            if category and formdef:
                return self.__class__(component, parent_category=category)
        elif self.parent_category and self.formdef.category_id != str(self.parent_category.id):
            # do not traverse further with an invalid category in path
            raise errors.TraversalError()

        try:
            filled = self.formdef.data_class().get(component)
        except KeyError:
            raise errors.TraversalError()

        return self.get_status_page_class()(self.formdef, filled, parent_view=self)


class RootDirectory(AccessControlled, Directory):
    _q_exports = ['', 'json', 'categories', 'code', 'tryauth', 'auth']

    category = None
    code = TrackingCodesDirectory()

    def __init__(self, category=None):
        self.category = category
        get_publisher().substitutions.feed(category)

    def tryauth(self):
        if self.category:
            base_url = self.category.get_url()
        else:
            base_url = get_publisher().get_root_url()
        return tryauth(base_url)

    def auth(self):
        if self.category:
            base_url = self.category.get_url()
        else:
            base_url = get_publisher().get_root_url()
        return auth(base_url)

    def _q_access(self):
        if self.category:
            response = get_response()
            response.breadcrumb.append(('%s/' % self.category.url_name, self.category.name))

    def get_list_of_forms(self, formdefs, user):
        list_forms = []
        advertised_forms = []

        for formdef in formdefs:
            if formdef.roles:
                if not user:
                    if formdef.always_advertise:
                        advertised_forms.append(formdef)
                    continue
                if logged_users_role().id not in formdef.roles:
                    for q in user.get_roles():
                        if q in formdef.roles:
                            break
                    else:
                        if formdef.always_advertise:
                            advertised_forms.append(formdef)
                        continue
            list_forms.append(formdef)

        return list_forms, advertised_forms

    def _q_index(self):
        if get_request().get_header('Accept', '') == 'application/json':
            return self.json()

        if not self.category:
            redirect_url = get_cfg('misc', {}).get('homepage-redirect-url')
        else:
            redirect_url = self.category.redirect_url or '/'

        if redirect_url:
            return redirect(
                misc.get_variadic_url(
                    redirect_url, get_publisher().substitutions.get_context_variables(mode='lazy')
                )
            )

        get_response().set_title(_('Forms'))
        r = TemplateIO(html=True)

        session = get_session()
        request = get_request()
        user = request.user

        if user:
            message = TextsDirectory.get_html_text('welcome-logged')
        else:
            message = TextsDirectory.get_html_text('welcome-unlogged')

        if message:
            r += htmltext('<div id="welcome-message">')
            r += message
            r += htmltext('</div>')

        all_formdefs = FormDef.select(order_by='name', ignore_errors=True)
        formdefs = [x for x in all_formdefs if (not x.is_disabled() or x.disabled_redirection)]

        if any(x for x in formdefs if x.enable_tracking_codes):
            r += htmltext('<div id="side">')
            r += htmltext('<div id="tracking-code">')
            r += htmltext('<h3>%s</h3>') % _('Tracking code')
            r += htmltext('<form action="/code/load" method="POST">')
            r += htmltext('<input size="12" name="code" placeholder="%s"/>') % _('ex: RPQDFVCD')
            r += htmltext('<button>%s</button>') % _('Load')
            r += htmltext('</form>')
            r += htmltext('</div>')
            r += htmltext('</div> <!-- #side -->')

        list_forms, advertised_forms = self.get_list_of_forms(formdefs, user)

        if formdefs and not list_forms and not advertised_forms:
            # there is forms, but none can be displayed
            raise errors.AccessUnauthorizedError()

        user_forms = []
        if user:
            from wcs.sql import AnyFormData

            user_forms = AnyFormData.select([Equal('user_id', str(user.id))], order_by='receipt_time')

        cats = Category.select()
        Category.sort_by_position(cats)
        one = False
        for c in cats:
            l2 = [x for x in list_forms if str(x.category_id) == str(c.id)]
            l2_advertise = [x for x in advertised_forms if str(x.category_id) == str(c.id)]
            if l2 or l2_advertise:
                r += self.form_list(
                    l2, category=c, session=session, user_forms=user_forms, advertised_forms=l2_advertise
                )
                one = True

        l2 = [x for x in list_forms if not x.category]
        l2_advertise = [x for x in advertised_forms if not x.category]
        if l2 or l2_advertise:
            if one:
                title = _('Misc')
            else:
                title = None
            r += self.form_list(
                l2, title=title, session=session, user_forms=user_forms, advertised_forms=l2_advertise
            )

        root_url = get_publisher().get_root_url()
        if user:
            r += self.user_forms(user_forms)

            r += htmltext('<p id="logout">')
            if user.can_go_in_backoffice():
                r += htmltext('<a href="%sbackoffice/">%s</a> - ') % (root_url, _('Back Office'))
            r += htmltext('<a href="%slogout">%s</a></p>') % (root_url, _('Logout'))

        elif get_cfg('sp') or get_cfg('identification', {}).get('methods'):
            r += htmltext('<p id="login"><a href="%slogin">%s</a>') % (root_url, _('Login'))
            identities_cfg = get_cfg('identities', {})
            if identities_cfg.get('creation') in ('self', 'moderated'):
                r += htmltext(' - <a href="%sregister">%s</a>') % (root_url, _('Register'))
            r += htmltext('</p>')
        return r.getvalue()

    def user_forms(self, user_forms):
        from wcs.sql import AnyFormData

        r = TemplateIO(html=True)

        def print_section(r, title, forms):
            if forms:
                r += htmltext('<h2 id="drafts">%s</h2>') % _(title)
                r += htmltext('<ul>')
                for f in forms:
                    r += htmltext('<li><a href="/%s/%s/">%s</a>, %s</li>') % (
                        f.formdef.slug,
                        f.id,
                        get_publisher().translate(f.formdef.name),
                        misc.localstrftime(f.receipt_time),
                    )
                r += htmltext('</ul>')

        drafts = [x for x in user_forms if x.is_draft() and not x.formdef.is_disabled()]
        pending = AnyFormData.select(
            [
                Equal('user_id', str(get_request().user.id)),
                Equal('is_at_endpoint', False),
                NotEqual('status', 'draft'),
            ],
            order_by='receipt_time',
        )
        done = AnyFormData.select(
            [
                Equal('user_id', str(get_request().user.id)),
                Equal('is_at_endpoint', True),
                NotEqual('status', 'draft'),
            ],
            order_by='receipt_time',
        )

        print_section(r, _('Your Current Drafts'), drafts)
        print_section(r, _('Your Current Forms'), pending)
        print_section(r, _('Your Past Forms'), done)

        return r.getvalue()

    def form_list(
        self, list, category=None, title=None, session=None, user_forms=None, advertised_forms=None
    ):
        advertised_forms = advertised_forms or []
        r = TemplateIO(html=True)

        keywords = {}
        for formdef in list:
            for keyword in formdef.keywords_list:
                keywords[keyword] = True

        div_attrs = {'class': 'category'}
        if keywords:
            div_attrs['data-keywords'] = ' '.join(keywords)

        if title:
            div_attrs['id'] = 'category-%s' % misc.simplify(title)
        elif category:
            div_attrs['id'] = 'category-%s' % category.url_name
        else:
            div_attrs['id'] = 'category-misc'

        r += htmltext('<div %s>' % ' '.join(['%s="%s"' % x for x in div_attrs.items()]))
        if title:
            r += htmltext('<h2>%s</h2>') % title
        elif category:
            r += htmltext('<h2>%s</h2>') % get_publisher().translate(category.name)

        formdefs_data = None
        if category:
            url_prefix = '%s/' % category.url_name
        else:
            url_prefix = ''
        r += htmltext('<ul class="catforms">')
        for formdef in list:
            if formdef.only_allow_one and user_forms:
                if formdefs_data is None:
                    formdefs_data = [
                        x.formdef.id for x in user_forms if x.formdef.only_allow_one and not x.is_draft()
                    ]
            r += htmltext('<li data-keywords="%s">') % ' '.join(formdef.keywords_list)
            if formdefs_data and formdef.id in formdefs_data:
                # form has already been completed
                r += htmltext('%s (%s, <a href="%s%s/">%s</a>)') % (
                    get_publisher().translate(formdef.name),
                    _('already completed'),
                    url_prefix,
                    formdef.url_name,
                    _('review'),
                )
            else:
                classes = []
                if formdef.is_disabled() and formdef.disabled_redirection:
                    classes.append('redirection')
                r += htmltext('<a class="%s" href="%s%s/">%s</a>') % (
                    ' '.join(classes),
                    url_prefix,
                    formdef.url_name,
                    get_publisher().translate(formdef.name),
                )

            if formdef.description:
                r += htmltext(
                    '<div class="description">%s</div>' % get_publisher().translate(formdef.description)
                )
            r += htmltext('</li>')

        for formdef in advertised_forms:
            r += htmltext('<li class="required-authentication" data-keywords="%s">') % ' '.join(
                formdef.keywords_list
            )
            r += htmltext('<a href="%s%s/">%s</a>') % (
                url_prefix,
                formdef.url_name,
                get_publisher().translate(formdef.name),
            )
            r += htmltext('<span> (%s)</span>') % _('authentication required')
            if formdef.description:
                r += htmltext(
                    '<div class="description">%s</div>' % get_publisher().translate(formdef.description)
                )
            r += htmltext('</li>')
        r += htmltext('</ul>')
        r += htmltext('</div>')
        return r.getvalue()

    def json(self):
        # backward compatibility
        from wcs.api import ApiFormdefsDirectory

        return ApiFormdefsDirectory(self.category)._q_index()

    def get_categories(self, user):
        result = []
        formdefs = [
            x
            for x in FormDef.select(
                order_by='name',
                ignore_errors=True,
                lightweight=True,
            )
            if not x.is_disabled() or x.disabled_redirection
        ]
        list_forms, advertised_forms = self.get_list_of_forms(formdefs, user)
        list_forms = list_forms + advertised_forms
        cats = Category.select()
        Category.sort_by_position(cats)
        for c in cats:
            if [x for x in list_forms if str(x.category_id) == str(c.id)]:
                result.append(c)
        return result

    def categories(self):
        if self.category:
            raise errors.TraversalError()
        if get_request().is_json():
            return self.categories_json()
        get_response().set_title(_('Categories'))
        r = TemplateIO(html=True)
        user = get_request().user
        for category in self.get_categories(user):
            r += htmltext('<h2>%s</h2>') % category.name
            r += category.get_description_html_text()
            r += htmltext('<p><a href="%s/">%s</a></p>') % (category.url_name, _('All forms'))
        return r.getvalue()

    def categories_json(self):
        # backward compatibility
        from wcs.api import ApiCategoriesDirectory

        return ApiCategoriesDirectory()._q_index()

    def _q_lookup(self, component):
        return FormPage(component, parent_category=self.category)


class PublicFormStatusPage(FormStatusPage):
    _q_exports_orig = [
        '',
        'download',
        'status',
        'live',
        'tempfile',
        'tsupdate',
        ('check-workflow-progress', 'check_workflow_progress'),
        'scan',
    ]
    form_page_class = FormPage
    history_templates = ['wcs/front/formdata_history.html', 'wcs/formdata_history.html']
    status_templates = ['wcs/front/formdata_status.html', 'wcs/formdata_status.html']

    def __init__(self, *args, **kwargs):
        FormStatusPage.__init__(self, *args, **kwargs)
        if self.filled.anonymised:
            if get_session() and get_session().is_anonymous_submitter(self.filled):
                return
            raise errors.TraversalError()

    def status(self):
        return redirect(
            '%sbackoffice/%s/%s/'
            % (get_publisher().get_root_url(), self.formdef.url_name, str(self.filled.id))
        )


TextsDirectory.register(
    'welcome-logged',
    _('Welcome text on home page for logged users'),
    condition=lambda: not get_cfg('misc', {}).get('homepage-redirect-url'),
)

TextsDirectory.register(
    'welcome-unlogged',
    _('Welcome text on home page for unlogged users'),
    condition=lambda: not get_cfg('misc', {}).get('homepage-redirect-url'),
)

TextsDirectory.register(
    'captcha-page',
    _('Explanation text before the CAPTCHA'),
    default=_(
        '''<h3>Verification</h3>

<p>
In order to submit the form you need to complete this simple question.
</p>'''
    ),
    condition=lambda: get_publisher().has_site_option('formdef-captcha-option'),
)


TextsDirectory.register(
    'form-recorded',
    _('Message when a form has been recorded'),
    category=_('Forms'),
    default=_(
        '''
The form has been recorded on {{ form_receipt_datetime }} with the number {{ form_number }}.
{% if form_submission_agent_display_name %}
It has been submitted for you by {{ form_submission_agent_display_name }}
{% if form_submission_channel == "phone" %}after a phone call.
{% elif form_submission_channel == "email" %}after an email.
{% elif form_submission_channel == "mail" %}after a mail.
{% elif form_submission_channel == "social-network" %}after a message on a social network.
{% elif form_submission_channel == "counter" %}after your passage at the counter.
{% else %}.
{% endif %}
{% endif %}
              '''
    ),
)

TextsDirectory.register(
    'form-recorded-allow-one',
    _('Message when a form has been recorded, and the form is set to only allow one per user'),
    category=_('Forms'),
    default=_(
        '''
The form has been recorded on {{ form_receipt_datetime }}.
{% if form_submission_agent_display_name %}
It has been submitted for you by {{ form_submission_agent_display_name }}
{% if form_submission_channel == "phone" %}after a phone call.
{% elif form_submission_channel == "email" %}after an email.
{% elif form_submission_channel == "mail" %}after a mail.
{% elif form_submission_channel == "social-network" %}after a message on a social network.
{% elif form_submission_channel == "counter" %}after your passage at the counter.
{% else %}.
{% endif %}
{% endif %}
              '''
    ),
)

TextsDirectory.register(
    'check-before-submit',
    _('Message when a form is displayed before validation'),
    category=_('Forms'),
    default=_('Check values then click submit.'),
)

TextsDirectory.register(
    'tracking-code-short-text',
    _('Short text in the tracking code box'),
    category=_('Forms'),
)
