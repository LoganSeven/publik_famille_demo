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

import base64
import csv
import itertools
import json
import pickle
import random

from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import BadRequest, PermissionDenied, ValidationError
from django.db import transaction
from django.forms import MediaDefiningClass
from django.http import Http404, HttpResponse
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_str
from django.utils.functional import cached_property
from django.utils.timezone import now
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, DetailView, FormView, TemplateView, UpdateView, View
from django.views.generic.base import ContextMixin
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import FormMixin
from django_select2.views import AutoResponseView
from django_tables2 import SingleTableMixin, SingleTableView
from gadjo.templatetags.gadjo import xstatic

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.backends import ldap_backend
from authentic2.data_transfer import ImportContext, export_site, import_site
from authentic2.decorators import json as json_view
from authentic2.forms.profile import modelform_factory
from authentic2.utils import crypto, hooks
from authentic2.utils.misc import batch_queryset, is_ajax, redirect

from . import app_settings, forms, utils, widgets


class MediaMixinBase(MediaDefiningClass, FormMixin):
    pass


class MultipleOUMixin:
    '''Tell templates if there are multiple OU for adaptation in breadcrumbs for example'''

    def get_context_data(self, **kwargs):
        kwargs['multiple_ou'] = utils.get_ou_count() > 1
        return super().get_context_data(**kwargs)


class MediaMixin(metaclass=MediaMixinBase):
    '''Expose needed CSS and JS files as a media object'''

    class Media:
        js = (
            xstatic('jquery.js', 'jquery.min.js'),
            reverse_lazy('a2-manager-javascript-catalog'),
            xstatic('jquery-ui.js', 'jquery-ui.min.js'),
            'js/gadjo.js',
            'jquery/js/jquery.form.js',
            'admin/js/urlify.js',
            'authentic2/js/purl.js',
            'authentic2/manager/js/manager.js',
        )
        css = {'all': ('authentic2/manager/css/style.css',)}

    def get_context_data(self, **kwargs):
        kwargs['media'] = self.media
        ctx = super().get_context_data(**kwargs)
        if 'form' in ctx:
            ctx['media'] += ctx['form'].media
        return ctx


class PermissionMixin:
    '''Control access to views based on permissions'''

    permissions = None
    permissions_global = False
    permission_model = None
    permission_pk_url_kwarg = None

    def authorize(self, request, *args, **kwargs):
        model = self.get_permission_model()
        if model and not self.permissions_global:
            app_label = model._meta.app_label
            model_name = model._meta.model_name
            add_perm = '%s.add_%s' % (app_label, model_name)
            self.can_add = request.user.has_perm_any(add_perm)
            permission_object = self.get_permission_object()
            if permission_object:
                self.object = permission_object
                permissions = ('view', 'change', 'delete', 'manage_members')
                for permission in permissions:
                    perm = '%s.%s_%s' % (app_label, permission, model_name)
                    base_value = True
                    if hasattr(permission_object, 'can_' + permission):
                        base_value = getattr(permission_object, 'can_' + permission, base_value)
                    setattr(
                        self,
                        'can_' + permission,
                        base_value and request.user.has_perm(perm, permission_object),
                    )
                if self.permissions and not request.user.has_perms(self.permissions, permission_object):
                    raise PermissionDenied
            elif self.permissions and not request.user.has_perm_any(self.permissions):
                raise PermissionDenied
        else:
            if self.permissions:
                if self.permissions_global and not request.user.has_perms(self.permissions):
                    raise PermissionDenied
                if not self.permissions_global and not request.user.has_perm_any(self.permissions):
                    raise PermissionDenied

    def get_permission_model(self):
        return self.permission_model or getattr(self, 'model', None)

    def get_permission_object(self):
        if self.permission_model and self.permission_pk_url_kwarg:
            try:
                return self.permission_model.objects.get(pk=self.kwargs[self.permission_pk_url_kwarg])
            except self.permission_model.DoesNotExist:
                raise Http404(
                    gettext('No %(verbose_name)s found matching the query')
                    % {'verbose_name': self.permission_model._meta.verbose_name}
                )
        elif hasattr(self, 'get_object') and (
            (hasattr(self, 'pk_url_kwarg') and self.pk_url_kwarg in self.kwargs)
            or (hasattr(self, 'slug_url_kwarg') and self.slug_url_kwarg in self.kwargs)
        ):
            return self.get_object()
        else:
            return None

    def dispatch(self, request, *args, **kwargs):
        response = self.authorize(request, *args, **kwargs)  # pylint: disable=assignment-from-no-return
        if response is not None:
            return response
        return super().dispatch(request, *args, **kwargs)


def filter_view(request, qs):
    model = qs.model
    perm = '%s.search_%s' % (model._meta.app_label, model._meta.model_name)
    return request.user.filter_by_perm(perm, qs)


class FilterQuerysetByPermMixin:
    def get_queryset(self):
        qs = super().get_queryset()
        return filter_view(self.request, qs)


class FilterTableQuerysetByPermMixin:
    def get_table_data(self):
        qs = super().get_table_data()
        if getattr(self, 'filter_table_by_perm', True):
            qs = filter_view(self.request, qs)
        return qs


class TableQuerysetMixin:
    def get_table_queryset(self):
        return self.get_queryset()

    def get_table_data(self):
        return self.get_table_queryset()


class SearchFormMixin:
    """Handle a search form on the current table view.

    The search form class must implement a .filter(qs) method returning a new queryset."""

    search_form_class = None

    def get_search_form_class(self):
        return self.search_form_class

    def get_search_form_kwargs(self):
        return {'request': self.request, 'data': self.request.GET.dict()}

    def get_search_form(self):
        form_class = self.get_search_form_class()
        if not form_class:
            return
        return form_class(**self.get_search_form_kwargs())

    def dispatch(self, request, *args, **kwargs):
        self.search_form = self.get_search_form()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.search_form:
            ctx['search_form'] = self.search_form
        return ctx

    def filter_by_search(self, qs):
        if self.search_form and self.search_form.is_valid():
            qs = self.search_form.filter(qs)
        return qs

    def get_table_data(self):
        qs = super().get_table_data()
        qs = self.filter_by_search(qs)
        return qs


class FormatsContextData:
    '''Export list of supported formats in context'''

    formats = ['csv']

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['formats'] = self.formats
        return ctx


class Action:
    '''Describe an action for view supporting multiples actions.'''

    name = None
    title = None
    confirm = None
    url_name = None
    url = None
    popup = True
    permission = None

    def __init__(
        self, name=None, title=None, confirm=None, url_name=None, url=None, popup=None, permission=None
    ):
        if name is not None:
            self.name = name
        if title is not None:
            self.title = title
        if confirm is not None:
            self.confirm = confirm
        if url_name is not None:
            self.url_name = url_name
        if url is not None:
            self.url = url
        if popup is not None:
            self.popup = popup
        if permission is not None:
            self.permission = permission

    def display(self, instance, request):
        if self.permission:
            return request.user.has_perm(self.permission, instance)
        return True


class AjaxFormViewMixin:
    '''Implement a JSON response for view which can be included in an AJAX popup'''

    success_url = '.'

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        return self.return_ajax_response(request, response)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['request_is_ajax'] = is_ajax(self.request)
        return ctx

    def return_ajax_response(self, request, response):
        if not is_ajax(request):
            return response
        data = {}
        if 'Location' in response:
            location = response['Location']
            # empty location means that the view can be used from anywhere
            # and so the redirect URL should not be used
            # otherwise compute an absolute URI from the relative URI
            if location and (
                not location.startswith('http://')
                or not location.startswith('https://')
                or not location.startswith('/')
            ):
                location = request.build_absolute_uri(location)
            data['location'] = location
        if hasattr(response, 'render'):
            response.render()
            data['content'] = force_str(response.content)
        return HttpResponse(json.dumps(data), content_type='application/json')


class TitleMixin:
    '''Mixin to provide a title to the view's template'''

    title = ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = self.title
        ctx['manager_site_title'] = app_settings.SITE_TITLE
        return ctx


class ActionMixin:
    '''Describe the main action implementd by a view'''

    action = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.action:
            ctx['action'] = self.action
        return ctx


class OtherActionsMixin:
    '''Describe secondary actions possible on a view'''

    other_actions = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['other_actions'] = tuple(self.get_displayed_other_actions())
        return ctx

    def get_other_actions(self):
        return self.other_actions or []

    def get_displayed_other_actions(self):
        actions = []
        other_actions = list(self.get_other_actions())
        hooks.call_hooks('manager_modify_other_actions', self, other_actions)
        for action in other_actions:
            if callable(action.display) and not action.display(self.object, self.request):
                continue

            if action.display:
                actions.append(action)
        return actions

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        for action in self.get_displayed_other_actions():
            if action.name in request.POST:
                response = None
                if hasattr(action, 'do'):
                    response = action.do(self, request, self.object)
                else:
                    method = getattr(self, 'action_' + action.name, None)
                    if method:
                        response = method(request, *args, **kwargs)
                hooks.call_hooks(
                    'event',
                    name='manager-action',
                    user=self.request.user,
                    action=action,
                    instance=self.object,
                )
                if response:
                    return response
                self.request.method = 'GET'
                return self.get(request, *args, **kwargs)
        parent = super()
        if hasattr(parent, 'post'):
            return parent.post(request, *args, **kwargs)
        return self.get(request, *args, **kwargs)


class ExportMixin:
    '''Help in implementd export views'''

    http_method_names = ['get', 'head', 'options']
    export_prefix = ''

    def get_export_prefix(self):
        return self.export_prefix

    def get_resource(self):
        return self.resource_class()

    def get_data(self):
        qs = self.get_table_data()
        return batch_queryset(qs)

    def get_dataset(self):
        return self.get_resource().export(self.get_data())

    def get(self, request, *args, **kwargs):
        if kwargs['format'].lower() != 'csv':
            raise Http404('unknown format')

        # use QUOTE_ALL to prevent CSV injection, see https://owasp.org/www-community/attacks/CSV_Injection
        content = self.get_dataset().get_csv(quoting=csv.QUOTE_ALL)

        return self.export_response(content=content, content_type='text/csv', export_format='csv')

    def export_response(self, content, content_type, export_format):
        response = HttpResponse(content, content_type=content_type)
        filename = '%s%s.%s' % (self.get_export_prefix(), now().strftime('%Y%m%d_%H%M%S'), export_format)
        response['Content-Disposition'] = 'attachment; filename="%s"' % filename
        return response


class FormNeedsRequest:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if getattr(self.get_form_class(), 'need_request', False):
            kwargs['request'] = self.request
        return kwargs


class ModelNameMixin(MediaMixin):
    '''Mixin to provide a model name to view's template'''

    def get_model_name(self):
        return self.model._meta.verbose_name

    def get_instance_name(self):
        if hasattr(self, 'get_object'):
            return str(self.get_object())
        return ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['model_name'] = self.get_model_name()
        return ctx


class TableHookMixin:
    '''Helper class for table views, hiding the OU column from tables if an OU filter exists'''

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        table.view = self
        hooks.call_hooks('manager_modify_table', self, table)
        return table


class BaseTableView(
    MultipleOUMixin,
    TitleMixin,
    TableHookMixin,
    FormatsContextData,
    ModelNameMixin,
    PermissionMixin,
    SearchFormMixin,
    FilterQuerysetByPermMixin,
    TableQuerysetMixin,
    SingleTableView,
):
    '''Base class for views showing a table of objects'''


class SubTableViewMixin(
    TableHookMixin,
    FormatsContextData,
    ModelNameMixin,
    PermissionMixin,
    SearchFormMixin,
    FilterTableQuerysetByPermMixin,
    TableQuerysetMixin,
    SingleObjectMixin,
    SingleTableMixin,
    ContextMixin,
):
    '''Helper class for views showing a table of objects related to one object'''

    context_object_name = 'object'
    paginate_by = None


class SimpleSubTableView(TitleMixin, SubTableViewMixin, TemplateView):
    '''Base class for views showing a simple table of objects related to one object'''


class BaseSubTableView(MultipleOUMixin, TitleMixin, SubTableViewMixin, FormNeedsRequest, FormView):
    '''Base class for views showing a table of objects related to one object'''

    success_url = '.'


class BaseDeleteView(TitleMixin, ModelNameMixin, PermissionMixin, AjaxFormViewMixin, DeleteView):
    '''Base class for views implementing deletion of an object'''

    template_name = 'authentic2/manager/delete.html'
    context_object_name = 'object'

    @property
    def permissions(self):
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        return ['%s.delete_%s' % (app_label, model_name)]

    @property
    def title(self):
        return _('Delete %s') % self.get_instance_name()

    def get_success_url(self):
        return '../../'


class ModelFormView(MediaMixin, FormNeedsRequest):
    '''Base class for views showing a form for a model'''

    fields = None
    form_class = None

    def get_fields(self):
        return self.fields

    def get_form_class(self):
        return modelform_factory(self.model, form=self.form_class, fields=self.get_fields())

    def get_form(self, form_class=None):
        form = super().get_form(form_class=form_class)
        hooks.call_hooks('manager_modify_form', self, form)
        return form


class BaseDetailView(MultipleOUMixin, TitleMixin, ModelNameMixin, PermissionMixin, ModelFormView, DetailView):
    context_object_name = 'object'
    form_class = None

    @property
    def permissions(self):
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        return ['%s.view_%s' % (app_label, model_name)]

    def get_form(self):
        form_class = self.get_form_class()
        if getattr(form_class, 'need_request', False):
            form = form_class(request=self.request, instance=self.object)
        else:
            form = form_class(instance=self.object)
        for field in form.fields.values():
            widget = field.widget
            widget.attrs['disabled'] = ''
            if 'readonly' in widget.attrs:
                del widget.attrs['readonly']
        return form

    def get_context_data(self, **kwargs):
        form = self.get_form()
        hooks.call_hooks('manager_modify_form', self, form)
        kwargs['form'] = form
        ctx = super().get_context_data(**kwargs)
        return ctx


class BaseAddView(
    MultipleOUMixin, TitleMixin, ModelNameMixin, PermissionMixin, AjaxFormViewMixin, ModelFormView, CreateView
):
    '''Base class for views for adding an instance of a model'''

    template_name = 'authentic2/manager/form.html'
    success_view_name = None
    context_object_name = 'object'

    @property
    def permissions(self):
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        return ['%s.add_%s' % (app_label, model_name)]

    @property
    def title(self):
        return 'Add %s' % super().get_model_name()

    def get_success_url(self):
        return reverse(self.success_view_name, kwargs={'pk': self.object.pk})


class BaseEditView(
    MultipleOUMixin,
    SuccessMessageMixin,
    TitleMixin,
    ModelNameMixin,
    PermissionMixin,
    AjaxFormViewMixin,
    ModelFormView,
    UpdateView,
):
    '''Base class for views for editing an instance of a model'''

    template_name = 'authentic2/manager/form.html'
    context_object_name = 'object'

    @property
    def permissions(self):
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        return ['%s.change_%s' % (app_label, model_name)]

    @property
    def title(self):
        return _('Edit %s') % self.get_instance_name()

    def get_success_url(self):
        return '..'


class HomepageView(TitleMixin, PermissionMixin, MediaMixin, TemplateView):
    template_name = 'authentic2/manager/homepage.html'
    permissions = [
        'a2_rbac.search_role',
        'a2_rbac.search_organizationalunit',
        'auth.search_group',
        'custom_user.search_user',
    ]
    default_entries = [
        {
            'class': 'icon-identity-management',
            'href': reverse_lazy('a2-manager-homepage'),
            'label': _('Identity management'),
            'order': -1,
            'sub': False,
            'skip_homepage': True,
            'slug': 'identity-management',
        },
        {
            'class': 'icon-organizational-units',
            'href': reverse_lazy('a2-manager-ous'),
            'label': _('Organizational units'),
            'help_text': _('Organizational units are used to logically group users, roles and services.'),
            'order': 1,
            'permissions': 'a2_rbac.search_organizationalunit',
            'skip_menu': True,
            'slug': 'organizational-units',
        },
        {
            'class': 'icon-users',
            'href': reverse_lazy('a2-manager-users'),
            'label': _('Users'),
            'help_text': _('Users are the main actors in identity management.'),
            'order': -1,
            'permissions': 'custom_user.search_user',
            'sub': True,
            'slug': 'users',
        },
        {
            'class': 'icon-roles',
            'href': reverse_lazy('a2-manager-roles'),
            'label': _('Roles'),
            'help_text': _('Roles are used to give some user specific access rights.'),
            'order': -1,
            'permissions': 'a2_rbac.search_role',
            'sub': True,
            'slug': 'roles',
        },
        {
            'class': 'icon-services',
            'href': reverse_lazy('a2-manager-services'),
            'label': _('Services'),
            'help_text': _('Services are applications using this central authority for identities.'),
            'order': 1,
            'permissions': 'authentic2.search_service',
            'skip_menu': True,
            'slug': 'services',
        },
        {
            'label': _('Authentication frontends'),
            'slug': 'authn',
            'href': reverse_lazy('a2-manager-authenticators'),
            'permissions': 'authenticators.search_baseauthenticator',
            'place': 'sidebar',
        },
        {
            'label': _('Global journal'),
            'slug': 'journal',
            'href': reverse_lazy('a2-manager-journal'),
            'permissions': ['custom_user.view_user', 'a2_rbac.view_role'],
            'permissions_global': True,
            'place': 'sidebar',
        },
        {
            'label': _('Directory servers'),
            'slug': 'tech-info',
            'href': reverse_lazy('a2-manager-tech-info'),
            'permissions': 'superuser',
            'place': 'sidebar',
            'condition': lambda: bool(ldap_backend.LDAPBackend.get_config()),
        },
        {
            'label': _('API Clients'),
            'slug': 'api-clients',
            'href': reverse_lazy('a2-manager-api-clients'),
            'permissions': ['authentic2.admin_apiclient'],
            'place': 'sidebar',
        },
    ]

    def dispatch(self, request, *args, **kwargs):
        if app_settings.HOMEPAGE_URL:
            return redirect(request, app_settings.HOMEPAGE_URL)
        return super().dispatch(request, *args, **kwargs)

    def get_homepage_entries(self):
        entries = []
        for hook_entries in itertools.chain(
            self.default_entries, hooks.call_hooks('manager_homepage_entries', self)
        ):
            if not hasattr(hook_entries, 'append'):
                hook_entries = [hook_entries]
            for entry in hook_entries:
                permissions = entry.get('permissions')
                if permissions == 'superuser' and not self.request.user.is_superuser:
                    continue
                if permissions:
                    if entry.get('permissions_global'):
                        if not self.request.user.has_perms(permissions):
                            continue
                    else:
                        if not self.request.user.has_perm_any(permissions):
                            continue
                condition = entry.get('condition')
                if condition and not condition():
                    continue
                entries.append(entry)
        # use possible key order to sort
        # list.sort() is supposed to be a stable sort (already sorted entries
        # are kept in the same order)
        entries.sort(key=lambda d: d.get('order', 0))
        return entries

    def get_context_data(self, **kwargs):
        entries = []
        sidebar_entries = []
        for entry in self.get_homepage_entries():
            if entry.get('skip_homepage', False):
                continue
            if entry.get('place') == 'sidebar':
                sidebar_entries.append(entry)
            else:
                entries.append(entry)
        kwargs['entries'] = entries
        kwargs['sidebar_entries'] = sidebar_entries
        kwargs['bg_image_random'] = random.randint(1, 8)
        return super().get_context_data(**kwargs)


homepage = HomepageView.as_view()


class TechnicalInformationView(TitleMixin, MediaMixin, TemplateView):
    template_name = 'authentic2/manager/tech_info.html'
    title = _('Technical information')

    def get(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        import ldap

        backend = ldap_backend.LDAPBackend
        kwargs['ldap_list'] = []
        for block in backend.get_config():
            config = block.copy()
            try:
                conn = backend.get_connection(config, raises=True)
            except ldap.LDAPError as e:
                config['error'] = True
                config['errmsg'] = str(e)
                config['ldap_uri'] = getattr(e, 'url', config.get('url', ''))
            else:
                # retrieve ldap uri, not directly visible in configuration block
                config['ldap_uri'] = conn.get_option(ldap.OPT_URI)
            config['block'] = json.dumps(block, indent=2, ensure_ascii=False)
            # user filters need to be formatted to ldapsearch syntax
            config['user_filter'] = force_str(block.get('user_filter'), '').replace('%s', '*')
            config['sync_ldap_users_filter'] = (
                force_str(block.get('sync_ldap_users_filter'), '').replace('%s', '*').replace('%s', '*')
            )

            kwargs['ldap_list'].append(config)
        return super().get_context_data(**kwargs)


tech_info = TechnicalInformationView.as_view()


class MenuJson(HomepageView):
    def get(self, request, *args, **kwargs):
        menu_entries = []
        for entry in self.get_homepage_entries():
            if entry.get('place') == 'sidebar':
                continue
            if entry.get('skip_menu', False):
                continue
            menu_entries.append(
                {
                    'label': str(entry['label']),
                    'slug': entry.get('slug', ''),
                    'url': request.build_absolute_uri(str(entry['href'])),
                    'sub': entry.get('sub', False),
                }
            )
        return menu_entries


menu_json = json_view(MenuJson.as_view())


class HideOUColumnMixin:
    '''Helper class for table views, hiding the OU column from tables if an OU filter exists'''

    def get_table(self, **kwargs):
        exclude_ou = False
        if (
            hasattr(self, 'search_form')
            and self.search_form.is_valid()
            and self.search_form.cleaned_data.get('ou') is not None
        ):
            exclude_ou = True
        if OrganizationalUnit.objects.count() < 2:
            exclude_ou = True
        if exclude_ou:
            exclude = kwargs.setdefault('exclude', [])
            if 'ou' not in exclude:
                exclude.append('ou')
        return super().get_table(**kwargs)


class Select2View(AutoResponseView):
    '''Overrided default django-select2 view to enforce security checks on Select2 AJAX requests.'''

    def get_widget_or_404(self):
        if not self.request.user.is_authenticated or not hasattr(self.request.user, 'filter_by_perm'):
            raise Http404('Invalid user')
        field_data = self.kwargs.get('field_id', self.request.GET.get('field_id', None))
        if not field_data:
            raise BadRequest('Invalid ID')
        try:
            field_data = crypto.loads(field_data)
        except (crypto.SignatureExpired, crypto.BadSignature):
            raise Http404('Invalid or expired signature.')

        widget_class = field_data.get('class')
        if not widget_class or not hasattr(widgets, widget_class):
            raise Http404('Missing or unknown widget class.')
        widget = getattr(widgets, widget_class)()
        if not isinstance(
            widget, (widgets.SimpleModelSelect2Widget, widgets.SimpleModelSelect2MultipleWidget)
        ):
            raise Http404('Reference to invalid widget class')
        qs = widget.get_queryset()
        qs.query.where = pickle.loads(base64.b64decode(field_data['where_clause']))
        # check permissions again as current user may not be the one who obtained the field_id
        perm = '%s.search_%s' % (qs.model._meta.app_label, qs.model._meta.model_name)
        qs = self.request.user.filter_by_perm(perm, qs)
        widget.queryset = qs
        widget.view = self
        return widget


select2 = Select2View.as_view()


class SiteExport(View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return HttpResponse(json.dumps(export_site(), indent=4), content_type='application/json')


site_export = SiteExport.as_view()


class SiteImportView(MediaMixin, TitleMixin, FormView):
    form_class = forms.SiteImportForm
    template_name = 'authentic2/manager/import_form.html'
    success_url = reverse_lazy('a2-manager-homepage')
    title = _('Site Import')

    def form_valid(self, form):
        try:
            with transaction.atomic():
                import_site(form.cleaned_data['site_json'], ImportContext())
        except ValidationError as e:
            form.add_error('site_json', e)
            return self.form_invalid(form)

        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


site_import = SiteImportView.as_view()


class SearchOUMixin:
    @cached_property
    def ou(self):
        try:
            ou_id = int(self.request.GET['search-ou'])
        except (ValueError, KeyError):
            return None
        else:
            return OrganizationalUnit.objects.filter(pk=ou_id).first()

    def get_context_data(self, **kwargs):
        return super().get_context_data(ou=self.ou, **kwargs)


class PermissionDeniedView(MediaMixin, TemplateView):
    template_name = 'authentic2/manager/403.html'

    def render_to_response(self, context, **response_kwargs):
        response_kwargs['status'] = 403
        return super().render_to_response(context, **response_kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['exception'] = self.kwargs['exception']
        return context


permission_denied = PermissionDeniedView.as_view()
