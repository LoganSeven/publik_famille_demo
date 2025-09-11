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

import re
from functools import reduce

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from . import models

User = get_user_model()

QUOTED_RE = re.compile(r'^[a-z0-9_-]*:"[^"]*"$')
LEXER_RE = re.compile(r'([a-z0-9_-]*:(?:"[^"]*"|[^ ]*)|[^\s]*)\s*')


class SearchEngine:
    # https://stackoverflow.com/a/35894763/6686829
    q_true = ~Q(pk__in=[])
    q_false = Q(pk__in=[])

    def lexer(self, query_string):
        # quote can be used to passe string containing spaces to prefix directives, like :
        # username:"john doe", used anywhere else they are part of words.
        # ex. « john "doe » gives the list ['john', '"doe']
        for lexem in LEXER_RE.findall(query_string):
            if not lexem:
                continue
            if QUOTED_RE.match(lexem):
                lexem = lexem.replace('"', '')
            yield lexem

    def query(self, query_string):
        lexems = list(self.lexer(query_string))
        queries = list(self.lexems_queries(lexems))
        return reduce(Q.__and__, queries, self.q_true)

    def lexems_queries(self, lexems):
        unmatched_lexems = []
        for lexem in lexems:
            queries = list(self.lexem_queries(lexem))
            if queries:
                yield reduce(Q.__or__, queries)
            else:
                unmatched_lexems.append(lexem)
        if unmatched_lexems:
            query = self.unmatched_lexems_query(unmatched_lexems)
            if query:
                yield query
            else:
                yield self.q_false

    def unmatched_lexems_query(self, unmatched_lexems):
        return None

    def lexem_queries(self, lexem):
        yield from self.lexem_queries_by_prefix(lexem)

    def lexem_queries_by_prefix(self, lexem):
        if ':' not in lexem:
            return
        prefix = lexem.split(':', 1)[0]

        method_name = 'search_by_' + prefix.replace('-', '_')
        if not hasattr(self, method_name):
            return

        yield from getattr(self, method_name)(lexem[len(prefix) + 1 :])

    @classmethod
    def documentation(cls):
        yield _(
            'You can use colon terminated prefixes to make special searches, and you can use quote around the'
            ' suffix to preserve spaces.'
        )
        for name in dir(cls):
            documentation = getattr(cls, name + '_documentation', None)
            if documentation:
                yield documentation()


class JournalSearchEngine(SearchEngine):
    def search_by_session(self, session_id):
        yield Q(session__session_key__startswith=session_id)

    @classmethod
    def search_by_session_documentation(cls):
        return _(
            'You can use <tt>session:abcd</tt> to find all events related to the session whose key starts with <tt>abcd</tt>.'
        )

    def search_by_event(self, event_name):
        q = self.q_false
        for evd in models.EventTypeDefinition.search_by_name(event_name.lower()):
            q |= Q(type__name=evd.name)
        yield q

    @classmethod
    def search_by_event_documentation(cls):
        return _('You can use <tt>event:login</tt> to find all events of type <tt>login</tt>.')

    def query_for_users(self, users):
        return models.EventQuerySet._which_references_query(users)

    def search_by_email(self, email):
        users = User.objects.filter(email__icontains=email.lower())
        yield (self.query_for_users(users) | Q(data__email__icontains=email.lower()))

    @classmethod
    def search_by_email_documentation(cls):
        return _(
            'You can use <tt>email:jhon.doe@example.com</tt> or <tt>email:@example.com</tt> to find all events related '
            'to users whose email address contain the given string.</tt>.'
        )

    def search_by_username(self, lexem):
        users = User.objects.filter(username__iexact=lexem)
        yield (self.query_for_users(users) | Q(data__username__iexact=lexem.lower()))

    @classmethod
    def search_by_username_documentation(cls):
        return _(
            'You can use <tt>username:john</tt> to find all events related to users whose username is <tt>john</tt>.'
        )

    def search_by_api(self, lexem):
        yield Q(api=bool(lexem == 'true'))

    @classmethod
    def search_by_api_documentation(cls):
        return _('You can use <tt>api:true</tt> to find all events related to API calls.')
