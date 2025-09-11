# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

import functools
import uuid

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from django.views.generic.list import ListView

from authentic2 import app_settings
from authentic2.apps.journal.forms import JournalForm as BaseJournalForm
from authentic2.apps.journal.models import EventType, n_2_pairing
from authentic2.apps.journal.search_engine import JournalSearchEngine as BaseJournalSearchEngine
from authentic2.apps.journal.views import JournalView
from authentic2.custom_user.models import DeletedUser

from . import views

User = get_user_model()


class JournalSearchEngine(BaseJournalSearchEngine):
    def search_by_uuid(self, lexem):
        # by user uuid
        try:
            user_uuid = uuid.UUID(lexem)
        except ValueError:
            yield self.q_false
            return
        # check if uuid exists to go on the fast path
        # searching in DeletedUser is expensive
        if User.objects.filter(uuid=user_uuid.hex).exists():
            yield Q(user__uuid=user_uuid.hex)
        else:
            deleted_user_qs = DeletedUser.objects.filter(old_uuid=user_uuid.hex)
            yield from self._search_by_deleted_user(deleted_user_qs)

    @classmethod
    def search_by_uuid_documentation(cls):
        return _(
            'You can use <tt>uuid:1234</tt> to find all events related to user whose UUID is <tt>1234</tt>.'
        )

    unmatched = None

    def lexem_queries(self, lexem):
        queries = list(super().lexem_queries(lexem))
        if queries:
            yield from queries
        elif '@' in lexem:
            # fallback for raw email
            try:
                EmailValidator(lexem)
            except ValidationError:
                pass
            else:
                yield from super().lexem_queries('email:' + lexem)
                yield from super().lexem_queries('username:' + lexem)

    def unmatched_lexems_query(self, unmatched_lexems):
        fullname = ' '.join(lexem.strip() for lexem in unmatched_lexems if lexem.strip())
        if fullname:
            users = User.objects.find_duplicates(fullname=fullname, threshold=app_settings.A2_FTS_THRESHOLD)
            return self.query_for_users(users)

    def _search_by_deleted_user(self, deleted_user_qs):
        pks = list(deleted_user_qs.values_list('old_user_id', flat=True))
        if pks:
            yield Q(user_id__in=pks)
            user_ct = ContentType.objects.get_for_model(User)
            yield Q(reference_ids__contains=[n_2_pairing(user_ct.id, pk) for pk in pks])

    def search_by_email(self, email):
        yield from super().search_by_email(email)
        deleted_user_qs = DeletedUser.objects.filter(old_email=email)
        yield from self._search_by_deleted_user(deleted_user_qs)

    @classmethod
    def search_by_event_documentation(cls):
        return '%s %s' % (
            super().search_by_event_documentation(),
            '<a href="%s" rel="popup">%s</a>.'
            % (
                reverse('a2-manager-journal-event-types'),
                _('View available event types'),
            ),
        )

    def search_by_how(self, lexem):
        yield Q(data__how__startswith=lexem)

    @classmethod
    def search_by_how_documentation(cls):
        return _(
            'You can use <tt>how:france-connect</tt> to find all events related to FranceConnect. Other possible values are "saml", "oidc" and "password".'
        )


EVENT_TYPE_CHOICES = (
    ('', _('All')),
    (
        _('General'),
        (('.*user.deletion$', _('User deletions')),),
    ),
    (
        _('Users'),
        (
            ('login,user.creation,user.registration,sso,password', _('Connection & SSO')),
            ('password', _('Password')),
            (
                'password,manager.user.((de)?activation|password|profile|email),^user.deletion,^user.profile',
                _('Profile changes'),
            ),
        ),
    ),
    (
        _('Backoffice'),
        (
            ('manager', _('All')),
            ('manager.user', _('User management')),
            ('manager.role', _('Role management')),
            ('manager.service', _('Service management')),
        ),
    ),
)


class JournalForm(BaseJournalForm):
    search_engine_class = JournalSearchEngine

    event_type = forms.ChoiceField(label=_('Event type'), required=False, choices=EVENT_TYPE_CHOICES)

    def clean_event_type(self):
        patterns = self.cleaned_data['event_type'].split(',')
        qs_filter = functools.reduce(Q.__or__, (Q(name__regex=pattern) for pattern in patterns))
        self.cleaned_data['_event_type'] = EventType.objects.filter(qs_filter)
        return self.cleaned_data['event_type']

    def get_queryset(self, **kwargs):
        qs = super().get_queryset(**kwargs)

        event_type = self.cleaned_data.get('_event_type')
        if event_type is not None:
            qs = qs.filter(type__in=event_type)

        return qs

    def prefetcher(self, model, pks):
        if not issubclass(model, User):
            return
        for deleted_user in DeletedUser.objects.filter(old_user_id__in=pks):
            yield deleted_user.old_user_id, deleted_user


class BaseJournalView(views.TitleMixin, views.MediaMixin, views.MultipleOUMixin, JournalView):
    template_name = 'authentic2/manager/journal.html'
    title = _('Journal')
    form_class = JournalForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        date_hierarchy = ctx['date_hierarchy']
        if date_hierarchy.title:
            if date_hierarchy.day is not None:
                ctx['title'] = pgettext_lazy('single day period', 'Journal of %s') % date_hierarchy.title
            else:
                ctx['title'] = pgettext_lazy('month or year period', 'Journal of %s') % date_hierarchy.title
        return ctx


class GlobalJournalView(views.PermissionMixin, BaseJournalView):
    template_name = 'authentic2/manager/journal.html'
    permissions_global = True
    permissions = ['custom_user.view_user', 'a2_rbac.view_role']


journal = GlobalJournalView.as_view()


class JournalEventTypesView(views.TitleMixin, ListView):
    model = EventType
    template_name = 'authentic2/manager/journal_event_types.html'
    title = _('Journal event types')


journal_event_types = JournalEventTypesView.as_view()
