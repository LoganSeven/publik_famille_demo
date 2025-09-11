# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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

import datetime
import json

from django.apps import apps
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.forms.models import modelform_factory
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, DetailView, FormView, UpdateView
from django.views.generic.list import ListView

from authentic2.apps.journal.views import JournalViewWithContext
from authentic2.manager.journal_views import BaseJournalView
from authentic2.manager.views import MediaMixin, PermissionMixin, TitleMixin
from authentic2.utils.misc import get_authenticators

from . import forms
from .models import AuthenticatorImportError, BaseAuthenticator


class AuthenticatorsMixin(MediaMixin, TitleMixin, PermissionMixin):
    model = BaseAuthenticator
    permissions = ['authenticators.search_baseauthenticator']

    def get_queryset(self):
        return self.model.authenticators.all()


class AuthenticatorsView(AuthenticatorsMixin, ListView):
    template_name = 'authentic2/authenticators/authenticators.html'
    title = _('Authenticators')


authenticators = AuthenticatorsView.as_view()


class AuthenticatorAddView(AuthenticatorsMixin, CreateView):
    template_name = 'authentic2/authenticators/authenticator_add_form.html'
    title = _('New authenticator')
    form_class = forms.AuthenticatorAddForm

    def get_success_url(self):
        return reverse('a2-manager-authenticator-edit', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        resp = super().form_valid(form)
        self.request.journal.record('authenticator.creation', authenticator=form.instance)
        messages.info(self.request, _('Please note that your modification may take 1 minute to be visible.'))
        get_authenticators.clear_cache()
        return resp


add = AuthenticatorAddView.as_view()


class AuthenticatorDetailView(AuthenticatorsMixin, DetailView):
    def get_template_names(self):
        return self.object.manager_view_template_name

    @property
    def title(self):
        return str(self.object)


detail = AuthenticatorDetailView.as_view()


def build_tab_is_not_default(form):
    for field_name, field in form.fields.items():
        if field.initial is not None:
            initial_value = field.initial() if callable(field.initial) else field.initial
            if initial_value != form.initial.get(field_name):
                return True
        else:
            if bool(form.initial.get(field_name)):
                return True
    return False


class MultipleFormsUpdateView(UpdateView):
    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        kwargs['forms'] = kwargs.get('forms') or self.get_forms()
        return kwargs


class AuthenticatorEditView(AuthenticatorsMixin, MultipleFormsUpdateView):
    template_name = 'authentic2/authenticators/authenticator_edit_form.html'
    title = _('Edit authenticator')

    def get_forms(self):
        forms = []
        for label, form_class in self.object.manager_form_classes:
            form = form_class(**self.get_form_kwargs())
            form.tab_name = label
            form.tab_slug = slugify(label)
            form.is_not_default = build_tab_is_not_default(form)
            forms.append(form)
        return forms

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        forms = self.get_forms()

        all_valid = all(form.is_valid() for form in forms)
        if all_valid:
            for form in forms:
                form.save()
            self.request.journal.record('authenticator.edit', forms=forms)
            messages.info(request, _('Please note that your modification may take 1 minute to be visible.'))
            get_authenticators.clear_cache()
            return HttpResponseRedirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(forms=forms))


edit = AuthenticatorEditView.as_view()


class AuthenticatorDeleteView(AuthenticatorsMixin, DeleteView):
    template_name = 'authentic2/authenticators/authenticator_delete_form.html'
    title = _('Delete authenticator')
    success_url = reverse_lazy('a2-manager-authenticators')

    def dispatch(self, *args, **kwargs):
        if self.get_object().protected:
            raise PermissionDenied
        return super().dispatch(*args, **kwargs)

    def get_success_url(self):
        self.request.journal.record('authenticator.deletion', authenticator=self.get_object())
        return super().get_success_url()


delete = AuthenticatorDeleteView.as_view()


class AuthenticatorToggleView(AuthenticatorsMixin, DetailView):
    def dispatch(self, *args, **kwargs):
        self.authenticator = self.get_object()
        if self.authenticator.protected or not self.authenticator.has_valid_configuration():
            raise PermissionDenied
        return super().dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        if self.authenticator.enabled:
            self.authenticator.enabled = False
            self.authenticator.save()
            self.request.journal.record('authenticator.disable', authenticator=self.authenticator)
            message = _('Authenticator has been disabled.')
        else:
            self.authenticator.enabled = True
            self.authenticator.save()
            self.request.journal.record('authenticator.enable', authenticator=self.authenticator)
            message = _('Authenticator has been enabled.')

        messages.info(self.request, message)
        messages.info(request, _('Please note that your modification may take 1 minute to be visible.'))
        get_authenticators.clear_cache()
        return HttpResponseRedirect(self.authenticator.get_absolute_url())


toggle = AuthenticatorToggleView.as_view()


class AuthenticatorJournal(JournalViewWithContext, BaseJournalView):
    template_name = 'authentic2/authenticators/authenticator_journal.html'
    title = _('Journal of edits')

    @cached_property
    def context(self):
        return get_object_or_404(BaseAuthenticator.authenticators.all(), pk=self.kwargs['pk'])

    def get_events(self):
        return super().get_events().filter(type__name__startswith='authenticator')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['object'] = self.context
        return ctx


journal = AuthenticatorJournal.as_view()


class AuthenticatorLoginJournal(JournalViewWithContext, BaseJournalView):
    template_name = 'authentic2/authenticators/authenticator_journal.html'
    title = _('Journal of logins')

    @cached_property
    def context(self):
        return get_object_or_404(BaseAuthenticator.authenticators.all(), pk=self.kwargs['pk'])

    def get_events(self):
        return super().get_events().filter(type__name__startswith='user')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['object'] = self.context
        return ctx


login_journal = AuthenticatorLoginJournal.as_view()


class AuthenticatorExportView(AuthenticatorsMixin, DetailView):
    def get(self, request, *args, **kwargs):
        authenticator = self.get_object()

        response = HttpResponse(content_type='application/json')
        today = datetime.date.today()
        attachment = 'attachment; filename="export_{}_{}.json"'.format(
            authenticator.slug.replace('-', '_'), today.strftime('%Y%m%d')
        )
        response['Content-Disposition'] = attachment
        json.dump(authenticator.export_json(), response, indent=4)
        return response


export_json = AuthenticatorExportView.as_view()


class AuthenticatorImportView(AuthenticatorsMixin, FormView):
    form_class = forms.AuthenticatorImportForm
    template_name = 'authentic2/manager/import_form.html'
    title = _('Authenticator Import')

    def form_valid(self, form):
        try:
            self.authenticator, created = BaseAuthenticator.import_json(
                form.cleaned_data['authenticator_json']
            )
        except AuthenticatorImportError as e:
            form.add_error('authenticator_json', e)
            return self.form_invalid(form)

        if created:
            messages.success(self.request, _('Authenticator has been created.'))
        else:
            messages.success(self.request, _('Authenticator has been updated.'))

        messages.info(self.request, _('Please note that your modification may take 1 minute to be visible.'))
        get_authenticators.clear_cache()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('a2-manager-authenticator-detail', kwargs={'pk': self.authenticator.pk})


import_json = AuthenticatorImportView.as_view()


class AuthenticatorsOrderView(AuthenticatorsMixin, FormView):
    template_name = 'authentic2/authenticators/authenticators_order_form.html'
    title = _('Configure display order')
    form_class = forms.AuthenticatorsOrderForm
    success_url = reverse_lazy('a2-manager-authenticators')

    def form_valid(self, form):
        order_by_pk = {pk: i for i, pk in enumerate(form.cleaned_data['order'].split(','))}

        authenticators = list(self.get_queryset())
        for authenticator in authenticators:
            authenticator.order = order_by_pk[str(authenticator.pk)]

        BaseAuthenticator.objects.bulk_update(authenticators, ['order'])
        messages.info(self.request, _('Please note that your modification may take 1 minute to be visible.'))
        get_authenticators.clear_cache()
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['authenticators'] = self.get_queryset()
        return context

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(enabled=True)


order = AuthenticatorsOrderView.as_view()


class AuthenticatorRelatedObjectMixin(MediaMixin, TitleMixin, PermissionMixin):
    permissions = ['authenticators.admin_baseauthenticator']
    permission_model = BaseAuthenticator
    permission_pk_url_kwarg = 'authenticator_pk'

    def dispatch(self, request, *args, **kwargs):
        self.authenticator = get_object_or_404(
            BaseAuthenticator.authenticators.all(), pk=kwargs.get('authenticator_pk')
        )

        model_name = kwargs.get('model_name')
        if model_name not in (x._meta.model_name for x in self.authenticator.related_models):
            raise Http404()
        try:
            self.model = apps.get_model(self.authenticator._meta.app_label, model_name)
        except LookupError:
            self.model = apps.get_model('authenticators', model_name)

        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return (
            reverse('a2-manager-authenticator-detail', kwargs={'pk': self.authenticator.pk})
            + '#open:%s' % self.model._meta.model_name
        )

    @property
    def title(self):
        return self.model._meta.verbose_name


class AuthenticatorRelatedObjectFormViewMixin(AuthenticatorRelatedObjectMixin):
    def get_form_class(self):
        return modelform_factory(self.model, self.authenticator.related_object_form_class)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        kwargs['instance'].authenticator = self.authenticator
        return kwargs


class RelatedObjectAddView(AuthenticatorRelatedObjectFormViewMixin, CreateView):
    template_name = 'authentic2/manager/form.html'

    def form_valid(self, form):
        resp = super().form_valid(form)
        self.request.journal.record('authenticator.related_object.creation', related_object=form.instance)
        return resp


add_related_object = RelatedObjectAddView.as_view()


class RelatedObjectEditView(AuthenticatorRelatedObjectFormViewMixin, UpdateView):
    template_name = 'authentic2/manager/form.html'

    def form_valid(self, form):
        resp = super().form_valid(form)
        self.request.journal.record('authenticator.related_object.edit', form=form)
        return resp


edit_related_object = RelatedObjectEditView.as_view()


class RelatedObjectDeleteView(AuthenticatorRelatedObjectMixin, DeleteView):
    template_name = 'authentic2/authenticators/authenticator_delete_form.html'
    title = ''

    def get_success_url(self):
        self.request.journal.record('authenticator.related_object.deletion', related_object=self.get_object())
        return super().get_success_url()


delete_related_object = RelatedObjectDeleteView.as_view()
