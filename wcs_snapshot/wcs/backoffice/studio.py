# w.c.s. - web application for online forms
# Copyright (C) 2005-2019  Entr'ouvert
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

from quixote import get_publisher, get_request
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin.logged_errors import LoggedErrorsDirectory
from wcs.admin.trash import TrashDirectory
from wcs.backoffice.deprecations import DeprecationsDirectory
from wcs.backoffice.pagination import pagination_links
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon import _, errors, misc, pgettext, template
from wcs.qommon.form import get_response
from wcs.sql import AnyFormData
from wcs.sql_criterias import And, Contains, Equal, Less, Null, Or, StrictNotEqual
from wcs.utils import grep_strings
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall


class ChangesDirectory(Directory):
    _q_exports = ['']

    def _q_index(self):
        get_response().breadcrumb.append(('all-changes/', pgettext('studio', 'All changes')))
        get_response().set_title(pgettext('studio', 'All Changes'))
        limit = misc.get_int_or_400(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size')) or 20
        )
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))

        backoffice_root = get_publisher().get_backoffice_root()
        object_types = []
        if backoffice_root.is_accessible('workflows'):
            object_types += [Workflow, MailTemplate, CommentTemplate]
        if backoffice_root.is_accessible('forms'):
            object_types += [NamedDataSource, BlockDef, FormDef]
        if backoffice_root.is_accessible('workflows'):
            object_types += [NamedDataSource]
        if backoffice_root.is_accessible('settings'):
            object_types += [NamedDataSource, NamedWsCall]
        if backoffice_root.is_accessible('cards'):
            object_types += [CardDef]
        object_types = [ot.xml_root_node for ot in object_types]

        objects = []
        links = ''
        if get_publisher().snapshot_class:
            objects = get_publisher().snapshot_class.get_recent_changes(
                object_types=object_types, limit=limit, offset=offset
            )
            total_count = get_publisher().snapshot_class.count_recent_changes(object_types=object_types)
            links = pagination_links(offset, limit, total_count, load_js=False)

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/changes.html'],
            context={
                'objects': objects,
                'pagination_links': links,
            },
        )

    def is_accessible(self, user, traversal=False):
        return user.is_admin


class StudioDirectory(Directory):
    _q_exports = [
        '',
        'deprecations',
        ('logged-errors', 'logged_errors_dir'),
        ('all-changes', 'changes_dir'),
        ('ancient-forms', 'ancient_forms'),
        'search',
        'trash',
    ]

    deprecations = DeprecationsDirectory()
    changes_dir = ChangesDirectory()
    trash = TrashDirectory()

    def __init__(self):
        self.logged_errors_dir = LoggedErrorsDirectory(parent_dir=self)

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('studio/', _('Studio')))
        get_response().set_backoffice_section('studio')
        return super()._q_traverse(path)

    def _q_index(self):
        get_response().set_title(_('Studio'))
        extra_links = []
        backoffice_root = get_publisher().get_backoffice_root()
        object_types = []
        if backoffice_root.is_accessible('forms') and backoffice_root.forms.blocks.is_accessible():
            extra_links.append(('../forms/blocks/', _('Blocks of fields')))
        if backoffice_root.is_accessible('workflows'):
            object_types.append(Workflow)
            if backoffice_root.workflows.mail_templates.is_accessible():
                extra_links.append(('../workflows/mail-templates/', pgettext('studio', 'Mail templates')))
                object_types.append(MailTemplate)
            if backoffice_root.workflows.comment_templates.is_accessible():
                extra_links.append(
                    ('../workflows/comment-templates/', pgettext('studio', 'Comment templates'))
                )
                object_types.append(CommentTemplate)
        if backoffice_root.is_accessible('forms'):
            object_types += [BlockDef, FormDef]
            if backoffice_root.forms.data_sources.is_accessible():
                extra_links.append(('../forms/data-sources/', pgettext('studio', 'Data sources')))
                object_types.append(NamedDataSource)
        elif backoffice_root.is_accessible('workflows'):
            if backoffice_root.workflows.data_sources.is_accessible():
                extra_links.append(('../workflows/data-sources/', pgettext('studio', 'Data sources')))
            object_types += [NamedDataSource]
        elif backoffice_root.is_accessible('settings'):
            extra_links.append(('../settings/data-sources/', pgettext('studio', 'Data sources')))
            object_types += [NamedDataSource]
        if backoffice_root.is_accessible('settings'):
            extra_links.append(('../settings/wscalls/', pgettext('studio', 'Webservice calls')))
            object_types += [NamedWsCall]
        if backoffice_root.is_accessible('cards'):
            object_types += [CardDef]
        if backoffice_root.is_accessible('i18n') and get_publisher().has_i18n_enabled():
            extra_links.append(('../i18n/', pgettext('studio', 'Multilinguism')))
        if backoffice_root.is_accessible('journal'):
            extra_links.append(('../journal/', pgettext('studio', 'Audit Journal')))

        user = get_request().user

        is_global_accessible = get_publisher().get_backoffice_root().is_global_accessible
        if is_global_accessible('forms') and is_global_accessible('workflows'):
            criterias = []
            today = datetime.date.today()
            for formdef in FormDef.select(lightweight=True):
                delay = formdef.get_old_but_non_anonymised_warning_delay()
                criterias.append(
                    And(
                        [
                            Equal('formdef_id', formdef.id),
                            Less('last_update_time', today - datetime.timedelta(days=delay)),
                        ]
                    )
                )
            ancient_count = AnyFormData.count(
                [StrictNotEqual('status', 'draft'), Null('anonymised'), Or(criterias)]
            )
        else:
            ancient_count = None

        context = {
            'ancient_count': ancient_count,
            'has_sidebar': False,
            'extra_links': extra_links,
            'recent_errors': LoggedErrorsDirectory.get_errors(offset=0, limit=5)[0],
            'show_all_changes': get_publisher().snapshot_class and user and user.is_admin,
            'is_global_search_allowed': self.is_global_search_allowed(),
            'is_trash_allowed': self.trash.is_accessible(),
        }
        if get_publisher().snapshot_class:
            context['recent_objects'] = get_publisher().snapshot_class.get_recent_changes(
                object_types=[ot.xml_root_node for ot in object_types],
                user=get_request().user,
            )
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/studio.html'], context=context, is_django_native=True
        )

    def is_accessible(self, user, traversal=False):
        backoffice_root = get_publisher().get_backoffice_root()
        return (
            backoffice_root.is_accessible('forms')
            or backoffice_root.is_accessible('workflows')
            or backoffice_root.is_accessible('cards')
        )

    def is_global_search_allowed(self):
        for section in ('forms', 'workflows', 'cards'):
            if not get_publisher().get_backoffice_root().is_global_accessible(section):
                return False
        return True

    def search(self):
        if not self.is_global_search_allowed():
            raise errors.AccessUnauthorizedError()

        query = get_request().form.get('q')
        if get_request().form.get('ajax') and query:
            get_request().disable_error_notifications = True
            get_request().ignore_session = True
            get_response().raw = True
            results = {}

            class TooManyResults(Exception):
                pass

            def accumulate(source_url, value, source_name):
                if len(results) == 50:
                    raise TooManyResults()
                results[source_url] = source_name

            try:
                grep_strings(query, hit_function=accumulate)
                too_many = False
            except TooManyResults:
                too_many = True

            r = TemplateIO(html=True)
            if results:
                for source_url, source_name in results.items():
                    r += htmltext(f'<li><a href="{source_url}">%s</a></li>\n') % source_name
            else:
                r += htmltext('<li class="list-item-no-usage"><p>%s</p></li>') % _('Nothing found.')
            if too_many:
                r += htmltext('<li class="list-item-too-many"><p>%s</p></li>') % _(
                    'Results were limited to 50 hits.'
                )
            return r.getvalue()

        get_response().set_title(_('Studio Search'))
        get_response().breadcrumb.append(('search', _('Studio Search')))
        context = {'q': query}
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/studio-search.html'], context=context, is_django_native=True
        )

    def ancient_forms(self):
        is_global_accessible = get_publisher().get_backoffice_root().is_global_accessible
        if not (is_global_accessible('forms') and is_global_accessible('workflows')):
            raise errors.AccessUnauthorizedError()

        criterias = []
        today = datetime.date.today()
        for formdef in FormDef.select(lightweight=True):
            delay = formdef.get_old_but_non_anonymised_warning_delay()
            criterias.append(
                And(
                    [
                        Equal('formdef_id', formdef.id),
                        Less('last_update_time', today - datetime.timedelta(days=delay)),
                    ]
                )
            )
        counts = AnyFormData.counts([StrictNotEqual('status', 'draft'), Null('anonymised'), Or(criterias)])
        formdef_by_id = {x.id: x for x in FormDef.select([Contains('id', counts.keys())], lightweight=True)}
        formdef_counts = {}
        for formdef_id, formdef_count in counts.items():
            formdef_counts[formdef_by_id[formdef_id]] = formdef_count

        formdef_counts = {
            x: y for x, y in sorted(formdef_counts.items(), key=lambda x: misc.simplify(x[0].name))
        }

        context = {}
        context['formdef_counts'] = formdef_counts

        get_response().set_title(_('Ancient forms'))
        get_response().breadcrumb.append(('ancient-forms', _('Ancient forms')))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/ancient-forms.html'], context=context, is_django_native=True
        )
