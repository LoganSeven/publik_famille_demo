# w.c.s. - web application for online forms
# Copyright (C) 2005-2014  Entr'ouvert
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

import urllib.parse

from quixote import get_publisher, get_request, get_response
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _


def pagination_links(offset, limit, total_count, load_js=True):
    # make sure a limit is set
    limit = limit or 10
    # make sure limit is not too high
    default_limit = int(get_publisher().get_site_option('default-page-size') or 100)
    limit = min(limit, max(100, default_limit))
    if load_js:
        get_response().add_javascript(['wcs.listing.js'])
    # pagination
    r = TemplateIO(html=True)
    r += htmltext('<div id="page-links">')
    query = get_request().form.copy()
    if 'ajax' in query:
        del query['ajax']
    if offset > 0:
        # link to previous page
        query['offset'] = max(offset - limit, 0)
        query['limit'] = limit
        r += htmltext(
            '<a class="previous-page" data-limit="%s" data-offset="%s" href="?%s"><!--%s--></a>'
        ) % (limit, query['offset'], urllib.parse.urlencode(query, doseq=1), _('Previous Page'))
    else:
        r += htmltext('<span class="previous-page"><!--%s--></span>') % _('Previous Page')

    # display links to individual pages
    page_range = 7
    current_page = offset // limit + 1
    last_page = max((total_count - 1) // limit + 1, 1)
    start = max(current_page - (page_range // 2), 1)
    end = min(start + page_range - 1, last_page)
    page_numbers = list(range(start, end + 1))
    if not page_numbers:
        page_numbers = [1]
    if 1 not in page_numbers:
        page_numbers.insert(0, 1)
        if 2 not in page_numbers:
            page_numbers.insert(1, Ellipsis)
    if last_page not in page_numbers:
        if last_page - 1 not in page_numbers:
            page_numbers.append(Ellipsis)
        page_numbers.append(last_page)

    r += htmltext(' <span class="pages">')
    for page_number in page_numbers:
        if page_number is Ellipsis:
            r += htmltext(' <span class="ellipsis">&#8230;</span> ')
        else:
            query['offset'] = (page_number - 1) * limit
            if page_number == current_page:
                klass = 'current'
            else:
                klass = ''
            r += htmltext(' <a class="%s" data-limit="%s" data-offset="%s" href="?%s">%s</a> ') % (
                klass,
                limit,
                query['offset'],
                urllib.parse.urlencode(query, doseq=1),
                page_number,
            )
    r += htmltext('</span>')  # <!-- .pages -->

    if offset + limit < total_count:
        # link to next page
        query['offset'] = offset + limit
        query['limit'] = limit
        if 'ajax' in query:
            del query['ajax']
        r += htmltext('<a class="next-page" data-limit="%s" data-offset="%s" href="?%s"><!--%s--></a>') % (
            limit,
            query['offset'],
            urllib.parse.urlencode(query, doseq=1),
            _('Next Page'),
        )
    else:
        r += htmltext('<span class="next-page"><!--%s--></span>') % _('Next Page')

    r += htmltext(' <span class="displayed-range">(%s-%s/%s)</span> ') % (
        min(offset + 1, total_count),
        min((offset + limit, total_count)),
        total_count,
    )

    r += htmltext(' <span class="page-limit"><span class="per-page-label">%s</span>') % _('Per page: ')
    for page_size in (10, 20, 50, 100):
        query['limit'] = page_size
        query['offset'] = '0'
        if page_size == limit:
            r += htmltext('<span>%s</span>') % page_size
        else:
            r += htmltext('<a data-limit="%s" data-offset="0" href="?%s">%s</a>') % (
                page_size,
                urllib.parse.urlencode(query, doseq=1),
                page_size,
            )
        if page_size >= total_count:
            break
    r += htmltext('</span>')  # <!-- .page-limit -->

    r += htmltext('</div>')
    return r.getvalue()
