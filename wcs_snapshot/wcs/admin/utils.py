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

import time

from django.utils.module_loading import import_string
from quixote import get_publisher, get_request, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, errors
from wcs.qommon.misc import localstrftime


class BySlugDirectory(Directory):
    def __init__(self, klass):
        self.klass = klass

    def _q_lookup(self, component):
        try:
            obj = self.klass.get_by_slug(component, ignore_errors=False)
        except KeyError:
            raise errors.TraversalError()
        return redirect(obj.get_admin_url())


def last_modification_block(obj):
    r = TemplateIO(html=True)

    timestamp, user_id = obj.get_last_modification_info()
    if timestamp:
        warning_class = ''
        if (time.time() - timestamp.timestamp()) < 600:
            if get_request().user and str(get_request().user.id) != user_id:
                warning_class = 'recent'
        r += htmltext('<p class="last-modification %s">') % warning_class
        r += str(_('Last Modification:'))
        r += ' '
        r += localstrftime(timestamp)
        r += ' '
        if user_id:
            try:
                r += str(_('by %s') % get_publisher().user_class.get(user_id).display_name)
            except KeyError:
                pass
        r += htmltext('</p>')

    return r.getvalue()


def snapshot_info_block(snapshot, url_name='view/', url_prefix='../../', url_suffix=''):
    r = TemplateIO(html=True)
    r += htmltext('<p>')
    parts = []
    if snapshot.label:
        parts.append(htmltext('<strong>%s</strong>') % snapshot.label)
    elif snapshot.comment:
        parts.append(snapshot.comment)
    if snapshot.user_id:
        parts.append('%s (%s)' % (localstrftime(snapshot.timestamp), snapshot.user))
    else:
        parts.append(localstrftime(snapshot.timestamp))
    r += htmltext('<br />').join(parts)
    r += htmltext('</p>')
    if snapshot.previous or snapshot.next:
        r += htmltext('<p class="snapshots-navigation">')
        if snapshot.id != snapshot.first:
            r += htmltext(
                f' <a class="button" href="{url_prefix}{snapshot.first}/{url_name}{url_suffix}">&Lt;</a>'
            )
            r += htmltext(
                f' <a class="button" href="{url_prefix}{snapshot.previous}/{url_name}{url_suffix}">&LT;</a>'
            )
        else:
            # currently browsing the first snapshot, display links as disabled
            r += htmltext(' <a class="button disabled" href="#">&Lt;</a>')
            r += htmltext(' <a class="button disabled" href="#">&LT;</a>')
        if snapshot.id != snapshot.last:
            r += htmltext(
                f' <a class="button" href="{url_prefix}{snapshot.next}/{url_name}{url_suffix}">&GT;</a>'
            )
            r += htmltext(
                f' <a class="button" href="{url_prefix}{snapshot.last}/{url_name}{url_suffix}">&Gt;</a>'
            )
        else:
            # currently browsing the last snapshot, display links as disabled
            r += htmltext(' <a class="button disabled" href="#">&GT;</a>')
            r += htmltext(' <a class="button disabled" href="#">&Gt;</a>')
        r += htmltext('</p>')

    r += htmltext('<div>')
    if snapshot.id == snapshot.first:
        r += htmltext(
            '<a class="button button-paragraph disabled" href="#" role="button" rel="popup">%s</a>'
        ) % (_('Restore version'),)
    else:
        r += htmltext(
            '<a class="button button-paragraph" href="%s%s/restore" role="button" rel="popup">%s</a>'
        ) % (
            url_prefix,
            snapshot.id,
            _('Restore version'),
        )
    r += htmltext('<a class="button button-paragraph" href="%s%s/export" role="button">%s</a>') % (
        url_prefix,
        snapshot.id,
        _('Export version'),
    )
    klass = snapshot.get_object_class()
    backoffice_class = import_string(klass.backoffice_class)
    has_inspect = hasattr(backoffice_class, 'render_inspect')
    if has_inspect and url_name != 'inspect':
        r += htmltext('<a class="button button-paragraph" href="%s%s/inspect" role="button">%s</a>') % (
            url_prefix,
            snapshot.id,
            _('Inspect version'),
        )
    r += htmltext('</div>')
    return r.getvalue()
