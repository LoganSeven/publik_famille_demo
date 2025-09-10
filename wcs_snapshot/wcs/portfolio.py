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

import base64
import json
import urllib.parse

from django.utils.encoding import force_str
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.api_utils import get_secret_and_orig, sign_url

from .qommon import _, errors
from .qommon.afterjobs import AfterJob
from .qommon.misc import http_post_request, urlopen


def has_portfolio():
    return get_publisher().get_site_option('fargo_url') is not None


def fargo_url(url):
    fargo_url = get_publisher().get_site_option('fargo_url')
    url = urllib.parse.urljoin(fargo_url, url)
    secret, orig = get_secret_and_orig(url)
    if '?' in url:
        url += '&orig=%s' % orig
    else:
        url += '?orig=%s' % orig
    return sign_url(url, secret)


class PushFargoAfterJob(AfterJob):
    def __init__(self, label, url, filename, payload, user_display_name):
        super().__init__(label=label)
        self.url = url
        self.filename = filename
        self.payload = payload
        self.user_display_name = user_display_name

    def execute(self):
        headers = {'Content-Type': 'application/json'}
        dummy, status, response_payload, dummy = http_post_request(
            self.url, json.dumps(self.payload), headers=headers
        )
        if status != 200:
            get_publisher().record_error(
                _(
                    'file %(filename)r failed to be pushed to portfolio of %(display_name)r '
                    '[status: %(status)d, payload: %(payload)r]'
                )
                % {
                    'filename': self.filename,
                    'display_name': self.user_display_name,
                    'status': status,
                    'payload': json.loads(response_payload),
                }
            )

        return status, json.loads(response_payload)


def push_document(user, filename, stream):
    if not user or not has_portfolio():
        return
    payload = {}
    if user.name_identifiers:
        payload['user_nameid'] = force_str(user.name_identifiers[0], 'ascii')
    elif user.email:
        payload['user_email'] = force_str(user.email, 'ascii')
    payload['origin'] = urllib.parse.urlparse(get_publisher().get_frontoffice_url()).netloc
    payload['file_name'] = filename
    stream.seek(0)
    payload['file_b64_content'] = force_str(base64.b64encode(stream.read()))

    url = fargo_url('/api/documents/push/')
    job = PushFargoAfterJob(
        _('Sending file %(filename)s in portfolio of %(user_name)s')
        % {'filename': filename, 'user_name': user.display_name},
        url,
        filename,
        payload,
        user.display_name,
    )
    if get_response():
        get_publisher().add_after_job(job)
    else:
        job.id = job.DO_NOT_STORE
        job.execute()


class FargoDirectory(Directory):
    _q_exports = ['pick']

    @property
    def fargo_url(self):
        return get_publisher().get_site_option('fargo_url')

    def pick(self):
        request = get_request()
        if 'url' in request.form:
            # Download file
            url = request.form['url']
            if not url.startswith(self.fargo_url):
                raise errors.AccessForbiddenError()
            try:
                document = urlopen(request.form['url']).read()
            except errors.ConnectionError:
                raise errors.TraversalError(_('Error downloading file'))
            path = urllib.parse.urlsplit(url)[2]
            path = path.split('/')
            name = urllib.parse.unquote(path[-1])
            from .qommon.upload_storage import PicklableUpload

            download = PicklableUpload(name, content_type='application/pdf')
            download.receive([document])
            tempfile = get_session().add_tempfile(download)
            get_response().set_header('X-Frame-Options', 'SameOrigin')
            return self.set_token(tempfile.get('token'), name)

        # Display file picker
        frontoffice_url = get_publisher().get_frontoffice_url()
        self_url = frontoffice_url
        self_url += '/fargo/pick'
        return redirect('%spick/?pick=%s' % (self.fargo_url, urllib.parse.quote(self_url)))

    def set_token(self, token, title):
        get_response().add_javascript(['jquery.js'])
        get_response().page_template_key = 'iframe'
        r = TemplateIO(html=True)
        r += htmltext('<html><body>')
        r += htmltext(
            '<script>window.top.document.fargo_set_token(%s, %s);</script>'
            % (json.dumps(token), json.dumps(title))
        )
        r += htmltext('</body></html>')
        return r.getvalue()
