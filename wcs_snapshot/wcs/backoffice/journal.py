# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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

import datetime

from quixote import get_publisher, get_request, get_response
from quixote.directory import Directory

from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon import _, template
from wcs.qommon.errors import AccessForbiddenError
from wcs.qommon.form import DateWidget, Form, OptGroup, SingleSelectWidget, StringWidget
from wcs.qommon.misc import get_as_datetime, get_int_or_400
from wcs.sql_criterias import Equal, Greater, GreaterOrEqual, Less, Nothing


class JournalDirectory(Directory):
    _q_exports = ['']

    def _q_traverse(self, path):
        if not get_publisher().get_backoffice_root().is_global_accessible('journal'):
            raise AccessForbiddenError()
        get_response().breadcrumb.append(('journal/', _('Audit Journal')))
        return super()._q_traverse(path)

    def _q_index(self):
        from wcs.audit import Audit

        get_response().set_title(_('Audit Journal'))
        context = {
            'has_sidebar': True,
            'html_form': self.get_filter_form(),
        }
        criterias = []
        querystring_parts = []
        order_by = '-id'
        if get_request().form.get('date'):
            try:
                dt = get_as_datetime(get_request().form.get('date'))
            except ValueError:
                criterias.append(Nothing())
            else:
                dtm = dt + datetime.timedelta(days=1)
                criterias.append(Less('timestamp', dtm))
                criterias.append(GreaterOrEqual('timestamp', dt))
            querystring_parts.append('date=%s' % get_request().form.get('date'))

        if get_request().form.get('action'):
            criterias.append(Equal('action', get_request().form.get('action')))
            querystring_parts.append('action=%s' % get_request().form.get('action'))

        if get_request().form.get('user_id'):
            criterias.append(Equal('user_id', get_request().form.get('user_id')))
            querystring_parts.append('user_id=%s' % get_request().form.get('user_id'))

        if get_request().form.get('object'):
            try:
                object_type, object_id = get_request().form.get('object').split(':')
            except ValueError:
                criterias.append(Nothing())
            else:
                criterias.append(Equal('object_type', object_type))
                criterias.append(Equal('object_id', object_id))
            querystring_parts.append('object=%s' % get_request().form.get('object'))

            formdata_id = get_request().form.get('object_id')
            if formdata_id:
                querystring_parts.append('object_id=%s' % formdata_id)
                try:
                    formdata_id = int(formdata_id)
                except ValueError:
                    criterias.append(Nothing())
                else:
                    criterias.append(Equal('data_id', formdata_id))

        first_id = Audit.get_first_id(criterias)  # take criterias without cursor
        if get_request().form.get('max'):
            criterias.append(Less('id', get_int_or_400(get_request().form.get('max'))))
        elif get_request().form.get('min'):
            criterias.append(Greater('id', get_int_or_400(get_request().form.get('min'))))
            order_by = 'id'
        context['lines'] = lines = Audit.select(criterias, order_by=order_by, limit=10)
        if order_by == 'id':
            lines.reverse()
        if len(lines) < 10 and get_request().form.get('min'):
            get_request().form['min'] = None
            return self._q_index()
        if lines:
            context['last_row_id'] = max(x.id for x in lines)
            context['first_row_id'] = min(x.id for x in lines)
        elif get_request().form.get('min'):
            get_request().form['min'] = None
            return self._q_index()

        if first_id in [x.id for x in lines]:
            # on latest page
            context['no_next'] = True

        if not get_request().form.get('min') and not get_request().form.get('max'):
            context['no_prev'] = True

        context['latest_page_id'] = first_id + 10
        context['extra_qs'] = '&'.join(querystring_parts)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/journal.html'], context=context, is_django_native=True
        )

    def get_filter_form(self):
        from wcs.audit import Audit

        get_response().add_javascript(['select2.js'])
        form = Form(method='get', action='.', id='journal-filter')
        form.add(DateWidget, 'date', title=_('Date'), date_in_the_past=True, date_can_be_today=True)

        user_options = [(None, '', '')]
        if get_request().form.get('user_id'):
            user = get_publisher().user_class.get(get_request().form.get('user_id'), ignore_errors=True)
            if user:
                user_options.append((user.id, str(user), user.id))
        form.add(
            SingleSelectWidget, 'user_id', title=_('User'), options=user_options, class_='user-selection'
        )

        formdefs = FormDef.select(order_by='name', lightweight=True, ignore_errors=True)
        carddefs = CardDef.select(order_by='name', lightweight=True, ignore_errors=True)
        object_options = [(None, '', '')]
        if formdefs and carddefs:
            object_options.append(OptGroup(_('Forms')))
        object_options.extend([(f'formdef:{x.id}', x.name, f'formdef:{x.id}') for x in formdefs])
        if formdefs and carddefs:
            object_options.append(OptGroup(_('Card Models')))
        object_options.extend([(f'carddef:{x.id}', x.name, f'carddef:{x.id}') for x in carddefs])
        form.add(SingleSelectWidget, 'object', title=_('Form/Card'), options=object_options)
        widget = form.add(StringWidget, 'object_id', title=_('Form/Card Identifier'))
        if not form.get_widget('object').parse():
            widget.is_hidden = True
        options = Audit.get_action_labels().items()
        if not get_publisher().get_site_storages():
            options = [x for x in options if x[0] != 'redirect to remote stored file']
        form.add(
            SingleSelectWidget,
            'action',
            title=_('Action'),
            options=[('', '', '')] + [(x[0], x[1], x[0]) for x in options],
        )
        form.add_submit('submit', _('Search'))
        return form
