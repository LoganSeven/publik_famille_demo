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
# authentic2

import base64
import functools
import inspect
import re
import secrets
import signal
import socket
import urllib.parse
from contextlib import closing, contextmanager

import pytest
from django.contrib.messages.storage.cookie import MessageDecoder, MessageEncoder

try:
    from django.contrib.messages.storage.cookie import MessageSerializer
except ImportError:  # oops, not running in django3
    import json

    class MessageSerializer:
        def dumps(self, obj):
            return json.dumps(
                obj,
                separators=(',', ':'),
                cls=MessageEncoder,
            ).encode('latin-1')

        def loads(self, data):
            return json.loads(data.decode('latin-1'), cls=MessageDecoder)


from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management import call_command as django_call_command
from django.shortcuts import resolve_url
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_str, iri_to_uri
from lxml import etree

from authentic2.apps.journal.models import Event
from authentic2.utils import misc as utils_misc

USER_ATTRIBUTES_SET = {
    'ou',
    'id',
    'uuid',
    'is_staff',
    'is_superuser',
    'first_name',
    'first_name_verified',
    'last_name',
    'last_name_verified',
    'date_joined',
    'last_login',
    'username',
    'email',
    'is_active',
    'modified',
    'email_verified',
    'email_verified_date',
    'email_verified_sources',
    'roles',
    'phone_verified_on',
    'last_account_deletion_alert',
    'deactivation',
    'deactivation_reason',
    'full_name',
}


def create_user(**kwargs):
    User = get_user_model()
    password = kwargs.pop('password', secrets.token_urlsafe(16))
    user, dummy = User.objects.get_or_create(**kwargs)
    if password:
        user.clear_password = password
        user.set_password(password)
        user.save()
    return user


def login(
    app,
    user,
    path=None,
    *,
    login=None,
    password=None,
    remember_me=None,
    ou=None,
    args=None,
    kwargs=None,
    fail=False,
):
    if path:
        args = args or []
        kwargs = kwargs or {}
        path = resolve_url(path, *args, **kwargs)
        login_page = app.get(path, status=302).maybe_follow()
    else:
        login_page = app.get(reverse('auth_login'))
    assert login_page.request.path == reverse('auth_login')
    form = login_page.forms['login-password-form']
    if not login:
        if hasattr(user, 'login'):
            login = user.login
        elif hasattr(user, 'username'):
            login = user.username
        else:
            login = user
    form.set('username', login)
    # password is supposed to be the same as username
    if not password:
        if hasattr(user, 'clear_password'):
            password = user.clear_password
        else:
            password = login
    assert form.get('password').attrs['autocomplete'] == 'current-password'
    form.set('password', password)
    if ou is not None and 'ou' in form.fields:
        form.set('ou', str(ou.pk))
    if remember_me is not None:
        form.set('remember_me', bool(remember_me))
    response = form.submit(name='login-password-submit')
    if fail:
        assert response.status_code == 200
        assert '_auth_user_id' not in app.session
    else:
        response = response.follow()
        if path:
            assert response.request.path == path
        else:
            assert response.request.path == reverse('auth_homepage')
        assert '_auth_user_id' in app.session
        assert not hasattr(user, 'id') or (app.session['_auth_user_id'] == str(user.id))
    return response


def logout(app):
    assert '_auth_user_id' in app.session
    response = app.get(reverse('auth_logout')).maybe_follow()
    response = response.form.submit().maybe_follow()
    if 'continue-link' in response.text:
        response = response.click('Continue logout').maybe_follow()
    assert '_auth_user_id' not in app.session
    return response


def basic_authorization_header(user_or_id, password=None):
    if isinstance(user_or_id, get_user_model()):
        username = user_or_id.username
        password = password or user_or_id.clear_password
    else:
        username = user_or_id
    cred = '%s:%s' % (username, password)
    b64_cred = base64.b64encode(cred.encode('utf-8'))
    return {'Authorization': 'Basic %s' % str(force_str(b64_cred))}


def basic_authorization_oidc_client(client):
    cred = f'{client.client_id}:{client.client_secret}'
    b64_cred = base64.b64encode(cred.encode('utf-8'))
    return {'Authorization': 'Basic %s' % str(force_str(b64_cred))}


def get_response_form(response, form='form'):
    contexts = list(response.context)
    for c in contexts:
        if form not in c:
            continue
        return c[form]


def assert_equals_url(url1, url2, **kwargs):
    """Check that url1 is equals to url2 augmented with parameters kwargs
    in its query string.

    The string '*' is a special value, when used it just check that the
    given parameter exist in the first url, it does not check the exact
    value.
    """
    url1 = iri_to_uri(utils_misc.make_url(url1, params=None))
    splitted1 = urllib.parse.urlsplit(url1)
    url2 = iri_to_uri(utils_misc.make_url(url2, params=kwargs))
    splitted2 = urllib.parse.urlsplit(url2)
    for i, (elt1, elt2) in enumerate(zip(splitted1, splitted2)):
        if i == 3:
            elt1 = urllib.parse.parse_qs(elt1, True)
            elt2 = urllib.parse.parse_qs(elt2, True)
            for k, v in elt1.items():
                elt1[k] = set(v)
            for k, v in elt2.items():
                if v == ['*']:
                    elt2[k] = elt1.get(k, v)
                else:
                    elt2[k] = set(v)
        assert elt1 == elt2, 'URLs are not equal: %s != %s' % (splitted1, splitted2)


def assert_redirects_complex(response, expected_url, **kwargs):
    assert response.status_code == 302, 'code should be 302'
    scheme, netloc, _, _, _ = urllib.parse.urlsplit(response.url)
    e_scheme, e_netloc, e_path, e_query, e_fragment = urllib.parse.urlsplit(expected_url)
    e_scheme = e_scheme if e_scheme else scheme
    e_netloc = e_netloc if e_netloc else netloc
    expected_url = urllib.parse.urlunsplit((e_scheme, e_netloc, e_path, e_query, e_fragment))
    assert_equals_url(response['Location'], expected_url, **kwargs)


def assert_xpath_constraints(xml, constraints, namespaces):
    if hasattr(xml, 'content'):
        xml = xml.content
    doc = etree.fromstring(xml)
    for xpath, content in constraints:
        nodes = doc.xpath(xpath, namespaces=namespaces)
        assert len(nodes) > 0, 'xpath %s not found' % xpath
        if isinstance(content, str):
            for node in nodes:
                if hasattr(node, 'text'):
                    assert node.text == content, 'xpath %s does not contain %s but %s' % (
                        xpath,
                        content,
                        node.text,
                    )
                else:
                    assert node == content, 'xpath %s does not contain %s but %s' % (xpath, content, node)
        else:
            values = [node.text if hasattr(node, 'text') else node for node in nodes]
            if isinstance(content, set):
                assert set(values) == content
            elif isinstance(content, list):
                assert values == content
            elif hasattr(content, 'pattern'):
                for value in values:
                    assert content.match(value), 'xpath %s does not match regexp %s' % (
                        xpath,
                        content.pattern,
                    )
            else:
                raise NotImplementedError(
                    'comparing xpath result to type %s: %r is not implemented' % (type(content), content)
                )


class Authentic2TestCase(TestCase):
    def assertEqualsURL(self, url1, url2, **kwargs):
        assert_equals_url(url1, url2, **kwargs)

    def assertRedirectsComplex(self, response, expected_url, **kwargs):
        assert_redirects_complex(response, expected_url, **kwargs)

    def assertXPathConstraints(self, xml, constraints, namespaces):
        assert_xpath_constraints(xml, constraints, namespaces)


@contextmanager
def check_log(caplog, message, levelname=None):
    idx = len(caplog.records)
    yield
    assert any(
        message in record.message
        for record in caplog.records[idx:]
        if not levelname or record.levelname == levelname
    ), ('%r not found in log records' % message)


def get_links_from_mail(mail):
    '''Extract links from mail sent by Django'''
    return re.findall('https?://[^ \n]*', mail.body)


def get_link_from_mail(mail):
    '''Extract the first and only link from this mail'''
    links = get_links_from_mail(mail)
    assert links, 'there is not link in this mail'
    assert len(links) == 1, 'there are more than one link in this mail'
    return links[0]


def saml_sp_metadata(base_url):
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<EntityDescriptor
 entityID="{base_url}/"
 xmlns="urn:oasis:names:tc:SAML:2.0:metadata">
 <SPSSODescriptor
   AuthnRequestsSigned="true"
   WantAssertionsSigned="true"
   protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
   <SingleLogoutService
     Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
     Location="https://files.entrouvert.org/mellon/logout" />
   <AssertionConsumerService
     index="0"
     isDefault="true"
     Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
     Location="{base_url}/sso/POST" />
   <AssertionConsumerService
     index="1"
     Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Artifact"
     Location="{base_url}/mellon/artifactResponse" />
 </SPSSODescriptor>
</EntityDescriptor>'''.format(
        base_url=base_url
    )


def find_free_tcp_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def request_select2(app, response, term='', fetch_all=False, page=1, get_kwargs=None):
    select2_url = response.pyquery('select')[0].attrib['data-ajax--url']
    select2_field_id = response.pyquery('select')[0].attrib['data-field_id']

    params = {'field_id': select2_field_id, 'term': term}
    if page:
        params['page'] = page

    select2_response = app.get(select2_url, params=params, **(get_kwargs or {}))
    if select2_response['content-type'] != 'application/json':
        return select2_response

    select2_json = select2_response.json
    results = select2_json['results']
    if fetch_all and select2_json['more']:
        results.extend(request_select2(app, response, term, fetch_all, page + 1, get_kwargs)['results'])

    return select2_json


@contextmanager
def run_on_commit_hooks():
    yield

    from django.db import connection

    current_run_on_commit = connection.run_on_commit
    connection.run_on_commit = []
    while current_run_on_commit:
        func = current_run_on_commit.pop(0)[1]
        func()


def call_command(*args, **kwargs):
    with run_on_commit_hooks():
        return django_call_command(*args, **kwargs)


def text_content(node):
    """Extract text content from node and all its children. Equivalent to
    xmlNodeGetContent from libxml."""
    return ''.join(node.itertext()) if node is not None else ''


def assert_event(event_type_name, user=None, session=None, service=None, target_user=None, api=False, **data):
    from authentic2.models import Service

    qs = Event.objects.filter(type__name=event_type_name, api=api)
    if user is not None:
        qs = qs.filter(user=user)
    else:
        qs = qs.filter(user__isnull=True)
    if session is not None:
        qs = qs.filter(session=session.session_key)
    else:
        qs = qs.filter(session__isnull=True)
    if service is not None:
        qs = qs.which_references(Service(pk=service.pk))
    # else:
    #    qs = qs.exclude(qs._which_references_query(models.Service))
    if target_user is not None:
        qs = qs.which_references(target_user)

    count = qs.count()
    assert count > 0

    if not data:
        assert count == 1

    if data and count == 1:
        event = qs.get()
        assert event.data, 'no event.data, should be %s' % data
        for key, value in data.items():
            assert event.data.get(key) == value, 'event.data[%s] != data[%s] (%s != %s)' % (
                key,
                key,
                event.data.get(key),
                value,
            )
        return event
    elif data and count > 1:
        qs = qs.filter(**{'data__' + k: v for k, v in data.items()})
        assert qs.count() == 1
        return qs.get()


def clear_events():
    Event.objects.all().delete()


def set_service(app, service):
    from importlib import import_module

    from django.conf import settings

    from authentic2.utils.service import _set_session_service

    engine = import_module(settings.SESSION_ENGINE)
    if app.session == {}:
        session = engine.SessionStore()
    else:
        session = app.session
    _set_session_service(session, service)
    session.save()
    if app.session == {}:
        app.set_cookie(settings.SESSION_COOKIE_NAME, session.session_key)


def decode_cookie(data):
    signer = signing.get_cookie_signer(salt='django.contrib.messages')
    try:
        return signer.unsign_object(data, serializer=MessageSerializer)
    except signing.BadSignature:
        return None
    except AttributeError:
        # xxx support legacy decoding?
        return data


def scoped_db_fixture(func=None, /, **kwargs):
    '''Create a db fixture with a scope different than 'function' the default one.'''
    if func is None:
        return functools.partial(scoped_db_fixture, **kwargs)
    assert kwargs.get('scope') in [
        'session',
        'module',
        'class',
    ], 'scoped_db_fixture is only usable with a non function scope'
    signature = inspect.signature(func)
    inner_parameters = []
    for parameter in signature.parameters:
        inner_parameters.append(parameter)
    outer_parameters = ['scoped_db'] if 'scoped_db' not in inner_parameters else []
    if inner_parameters and inner_parameters[0] == 'self':
        outer_parameters = ['self'] + outer_parameters + inner_parameters[1:]
    else:
        outer_parameters = outer_parameters + inner_parameters
    # build a new fixture function, inject the scoped_db fixture inside and the old function in a scoped_db context.
    new_function_def = f'''def f({', '.join(outer_parameters)}):
    if inspect.isgeneratorfunction(func):
        def g():
            yield from func({', '.join(inner_parameters)})
    else:
        def g():
            return func({', '.join(inner_parameters)})
    with scoped_db(g) as result:
        yield result'''
    new_function_bytecode = compile(new_function_def, filename=f'<scoped-db-fixture {func}>', mode='single')
    global_dict = {'func': func, 'inspect': inspect}
    eval(new_function_bytecode, global_dict)  # pylint: disable=eval-used
    new_function = global_dict['f']
    wrapped_func = functools.wraps(func)(new_function)
    # prevent original fixture signature to override the new fixture signature
    # (because of inspect.signature(follow_wrapped=True)) during pytest collection
    del wrapped_func.__wrapped__
    return pytest.fixture(**kwargs)(wrapped_func)


def get_memcache_config(port):
    return {
        'default': {
            'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
            'LOCATION': '127.0.0.1:%d' % port,
            'KEY_PREFIX': 'authentic2',
            'OPTIONS': {'ignore_exc': True},
        }
    }


def memcache_server(close_after, replies, port_queue):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        sock.settimeout(1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', 0))
    except OSError:
        sock.close()
        return
    sock.listen(1)
    port_queue.put(sock.getsockname()[1])

    def sighandler(signum, *args):
        sock.close()

    signal.signal(signal.SIGUSR1, sighandler)

    while True:
        try:
            conn, dummy = sock.accept()
        except OSError:
            break
        with conn:
            while True:
                recv_data = b''
                try:
                    recv_data = conn.recv(2048)
                except OSError:
                    pass
                if len(recv_data) == 0 or not close_after:
                    # Closes the client socket if it is closed
                    # or if we reached the close_after limit
                    break
                command = recv_data.split(b' ')[0].decode('utf-8')
                if command in replies and len(replies[command]):
                    reply = replies[command].pop(0)
                    replies[command].append(reply)
                    conn.send(('%s\r\n' % reply).encode())
                else:
                    conn.close()  # unexpected close
                close_after -= 1
    sock.close()
