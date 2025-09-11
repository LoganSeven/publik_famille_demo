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
# authentic2

from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.utils.functional import lazy

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.journal import Journal
from authentic2.utils.lazy import lazy_join
from authentic2.utils.misc import (
    authenticate,
    get_authentication_events,
    get_remember_cookie,
    good_next_url,
    login,
    prepend_remember_cookie,
    same_origin,
    select_next_url,
    send_templated_mail,
    user_can_change_password,
)


def test_good_next_url(db, rf, settings):
    request = rf.get('/', HTTP_HOST='example.net', **{'wsgi.url_scheme': 'https'})
    assert good_next_url(request, '/admin/')
    assert good_next_url(request, '/')
    assert good_next_url(request, 'https://example.net/')
    assert good_next_url(request, 'https://example.net:443/')
    assert not good_next_url(request, 'https://example.net:4443/')
    assert not good_next_url(request, 'http://example.net/')
    assert not good_next_url(request, 'https://google.com/')
    assert not good_next_url(request, '')
    assert not good_next_url(request, None)
    assert not good_next_url(request, '/\\example.com/')
    assert not good_next_url(request, '/\\example.net/')
    assert not good_next_url(request, '\\\\example.com/')
    assert not good_next_url(request, '\\\\example.net/')
    assert not good_next_url(request, '/\x0d/example.net/')


def test_good_next_url_backends(rf, external_redirect):
    next_url, valid = external_redirect
    request = rf.get('/', HTTP_HOST='example.net', **{'wsgi.url_scheme': 'https'})
    if valid:
        assert good_next_url(request, next_url)
    else:
        assert not good_next_url(request, next_url)


def test_same_origin():
    assert same_origin('http://example.com/coin/', 'http://example.com/')
    assert same_origin('http://example.com/coin/', 'http://example.com:80/')
    assert same_origin('http://example.com:80/coin/', 'http://example.com/')
    assert same_origin('http://example.com:80/coin/', 'http://.example.com/')
    assert same_origin('http://example.com:80/coin/', '//example.com/')
    assert not same_origin('https://example.com:80/coin/', 'http://example.com/')
    assert not same_origin('http://example.com/coin/', 'http://bob.example.com/')
    assert same_origin('https://example.com/coin/', 'https://example.com:443/')
    assert not same_origin('https://example.com:34/coin/', 'https://example.com/')
    assert same_origin('https://example.com:34/coin/', '//example.com')
    assert not same_origin('https://example.com/coin/', '//example.com:34')
    assert same_origin('https://example.com:443/coin/', 'https://example.com/')
    assert same_origin('https://example.com:34/coin/', '//example.com')


def test_select_next_url(db, rf, settings):
    def next_url(next_url, default='/'):
        request = rf.get('/register/', data={'next': next_url})
        return select_next_url(request, default=default)

    assert next_url('/admin/') == '/admin/'
    assert next_url('http://example.com/') == '/'
    assert next_url('/\x0d/example.com/') == '/'

    settings.A2_REDIRECT_WHITELIST = ['//example.com/']
    assert next_url('http://example.com/') == 'http://example.com/'


def test_user_can_change_password(simple_user, settings):
    assert user_can_change_password(user=simple_user) is True
    settings.A2_REGISTRATION_CAN_CHANGE_PASSWORD = False
    assert user_can_change_password(user=simple_user) is False


def test_get_authentication_events_hows(rf, simple_user):
    user = authenticate(username=simple_user.username, password=simple_user.clear_password)
    request = rf.get('/login/')

    def get_response():
        return None

    middleware = SessionMiddleware(get_response)
    middleware.process_request(request)
    middleware = AuthenticationMiddleware(get_response)
    middleware.process_request(request)
    MessageMiddleware(get_response).process_request(request)
    request.journal = Journal(request=request)
    assert 'password' not in [ev['how'] for ev in get_authentication_events(request)]
    login(request, user, 'password')
    assert 'password' in [ev['how'] for ev in get_authentication_events(request)]


def test_remember_cookie(rf):
    from django.http import HttpResponse

    request = rf.get('/')
    request.COOKIES['preferrence'] = '1 2'
    assert get_remember_cookie(request, 'preferrence') == [1, 2]
    request.COOKIES['preferrence'] = '1 2 3 4 5 6'
    assert get_remember_cookie(request, 'preferrence') == [1, 2, 3, 4, 5]
    request.COOKIES['preferrence'] = '1 2 3 4 x'
    assert not get_remember_cookie(request, 'preferrence')
    request.COOKIES['preferrence'] = 'aaa'
    assert not get_remember_cookie(request, 'preferrence')

    response = HttpResponse()
    request.COOKIES['preferrence'] = '1 2'
    prepend_remember_cookie(request, response, 'preferrence', 4)
    assert response.cookies['preferrence'].value == '4 1 2'

    response = HttpResponse()
    request.COOKIES['preferrence'] = '1 2 a'
    prepend_remember_cookie(request, response, 'preferrence', 4)
    assert response.cookies['preferrence'].value == '4'

    response = HttpResponse()
    request.COOKIES['preferrence'] = '1 2 3 4 5'
    prepend_remember_cookie(request, response, 'preferrence', 7)
    assert response.cookies['preferrence'].value == '7 1 2 3 4'


def test_send_templated_mail_template_selection(simple_user):
    ou = OrganizationalUnit.objects.create(slug='ou_name')
    simple_user.ou = ou
    default_template = 'default_mail_template'
    specific_template = 'custom_mail_template'
    default_template_ou = '_'.join((default_template, ou.slug))
    specific_template_ou = '_'.join((specific_template, ou.slug))
    template_names = [default_template]

    send_templated_mail(simple_user, template_names)
    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == default_template
    assert sent_mail.body == default_template

    send_templated_mail(simple_user, template_names, per_ou_templates=True)
    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == default_template_ou
    assert sent_mail.body == default_template_ou

    template_names.insert(0, specific_template)
    send_templated_mail(simple_user, template_names)
    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == specific_template
    assert sent_mail.body == specific_template

    send_templated_mail(simple_user, template_names, per_ou_templates=True)
    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == specific_template_ou
    assert sent_mail.body == specific_template_ou


def test_send_templated_mail_template_vars(settings, simple_user):
    settings.TEMPLATE_VARS = {
        'template_vars_subject_txt': 'here is the subject',
        'template_vars_body_txt': 'here is the text body',
        'template_vars_body_html': 'here is the html body',
    }
    send_templated_mail(simple_user, ['template_vars'])

    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == 'here is the subject'
    assert sent_mail.body == 'here is the text body\n'
    assert sent_mail.alternatives == [('here is the html body\n', 'text/html')]

    settings.A2_EMAIL_FORMAT = 'text/plain'
    send_templated_mail(simple_user, ['template_vars'])

    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == 'here is the subject'
    assert sent_mail.body == 'here is the text body\n'
    assert sent_mail.content_subtype == 'plain'
    assert not hasattr(sent_mail, 'alternatives')

    settings.A2_EMAIL_FORMAT = 'text/html'
    send_templated_mail(simple_user, ['template_vars'])

    assert len(mail.outbox) == 1
    sent_mail = mail.outbox.pop()
    assert sent_mail.subject == 'here is the subject'
    assert sent_mail.body == 'here is the html body\n'
    assert sent_mail.content_subtype == 'html'
    assert not hasattr(sent_mail, 'alternatives')


def test_lazy_join():
    a = 'a'

    def f():
        return a

    f = lazy(f, str)

    joined = lazy_join(',', ['a', f()])

    assert str(joined) == 'a,a'

    a = 'b'

    assert str(joined) == 'a,b'
