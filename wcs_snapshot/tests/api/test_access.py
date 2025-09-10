import base64
import datetime
import hashlib
import hmac
import os
import urllib.parse

import pytest
from django.utils.encoding import force_bytes

import wcs.api_access
from wcs.api_utils import get_secret_and_orig, is_url_signed, sign_url
from wcs.qommon.errors import AccessForbiddenError
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''\
[api-secrets]
coucou = 1234
'''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def no_request_pub():
    pub = create_temporary_pub()
    pub.app_dir = os.path.join(pub.APP_DIR, 'example.net')
    pub.set_config()

    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''
[wscall-secrets]
api.example.com = 1234
'''
        )
    return pub


def test_get_secret_and_orig(no_request_pub):
    secret, orig = get_secret_and_orig('https://api.example.com/endpoint/')
    assert secret == '1234'
    assert orig == 'example.net'


def test_user_page_redirect(pub):
    output = get_app(pub).get('/user')
    assert output.headers.get('location') == 'http://example.net/myspace/'


def test_user_page_error(pub):
    # check we get json as output for errors
    output = get_app(pub).get('/api/user/', status=403)
    assert output.json['err_desc'] == 'User not authenticated.'


def test_user_page_error_when_json_and_no_user(pub):
    output = get_app(pub).get('/api/user/?format=json', status=403)
    assert output.json['err_desc'] == 'User not authenticated.'


def test_get_user_from_api_query_string_error_missing_orig(pub):
    output = get_app(pub).get('/api/user/?format=json&signature=xxx', status=403)
    assert output.json['err_desc'] == 'Missing/multiple orig field.'


def test_get_user_from_api_query_string_error_invalid_orig(pub):
    output = get_app(pub).get('/api/user/?format=json&orig=coin&signature=xxx', status=403)
    assert output.json['err_desc'] == 'Invalid orig.'


def test_get_user_from_api_query_string_error_missing_algo(pub):
    output = get_app(pub).get('/api/user/?format=json&orig=coucou&signature=xxx', status=403)
    assert output.json['err_desc'] == 'Missing/multiple algo field.'


def test_get_user_from_api_query_string_error_invalid_algo(pub):
    output = get_app(pub).get('/api/user/?format=json&orig=coucou&signature=xxx&algo=coin', status=403)
    assert output.json['err_desc'] == 'Invalid algo.'
    output = get_app(pub).get(
        '/api/user/?format=json&orig=coucou&signature=xxx&algo=__getattribute__', status=403
    )
    assert output.json['err_desc'] == 'Invalid algo.'


def test_get_user_from_api_query_string_error_invalid_signature(pub):
    output = get_app(pub).get('/api/user/?format=json&orig=coucou&signature=xxx&algo=sha1', status=403)
    assert output.json['err_desc'] == 'Invalid signature.'


def test_get_user_from_api_query_string_error_missing_timestamp(pub):
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', b'format=json&orig=coucou&algo=sha1', hashlib.sha1).digest())
    )
    output = get_app(pub).get(
        '/api/user/?format=json&orig=coucou&algo=sha1&signature=%s' % signature, status=403
    )
    assert output.json['err_desc'] == 'Missing/multiple timestamp field.'


def test_get_user_from_api_query_string_error_delta_timestamp(pub):
    timestamp = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)).isoformat()[:19] + 'Z'
    query = 'format=json&orig=coucou&algo=sha1&timestamp=' + timestamp
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature), status=403)
    assert output.json['err_desc'].startswith('timestamp is more than 30 seconds in the past: 0:01:')

    timestamp = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).isoformat()[:19] + 'Z'
    query = 'format=json&orig=coucou&algo=sha1&timestamp=' + timestamp
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature), status=403)
    assert output.json['err_desc'].startswith('timestamp is more than 30 seconds in the future: 0:59:')


def test_get_user_from_api_query_string_error_missing_email(pub):
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = 'format=json&orig=coucou&algo=sha1&timestamp=' + timestamp
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature), status=403)
    assert output.json['err_desc'] == 'User not authenticated.'


def test_get_user_from_api_query_string_error_unknown_nameid(pub):
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = 'format=json&orig=coucou&algo=sha1&NameID=xxx&timestamp=' + timestamp
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature), status=403)
    assert output.json['err_desc'] == 'Unknown NameID.'


def test_get_user_from_api_query_string_error_missing_email_valid_endpoint(pub):
    # check it's ok to sign an URL without specifiying an user if the endpoint
    # works fine without user.
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = 'format=json&orig=coucou&algo=sha1&timestamp=' + timestamp
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/categories?%s&signature=%s' % (query, signature))
    assert output.json == {'data': []}
    output = get_app(pub).get('/json?%s&signature=%s' % (query, signature))
    assert output.json == {'err': 0, 'data': []}


def test_get_user_from_api_query_string_error_unknown_nameid_valid_endpoint(pub):
    # check the categories and forms endpoints accept an unknown NameID
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = 'format=json&NameID=xxx&orig=coucou&algo=sha1&timestamp=' + timestamp
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/categories?%s&signature=%s' % (query, signature))
    assert output.json == {'data': []}
    output = get_app(pub).get('/json?%s&signature=%s' % (query, signature))
    assert output.json == {'err': 0, 'data': []}


def test_get_user_from_api_query_string_error_success_sha1(pub, local_user):
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = (
        'format=json&orig=coucou&algo=sha1&email='
        + urllib.parse.quote(local_user.email)
        + '&timestamp='
        + timestamp
    )
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature))
    assert output.json['user_display_name'] == 'Jean Darmette'


def test_get_user_from_api_query_string_error_invalid_signature_algo_mismatch(pub, local_user):
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = (
        'format=json&orig=coucou&algo=sha256&email='
        + urllib.parse.quote(local_user.email)
        + '&timestamp='
        + timestamp
    )
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha1).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature), status=403)
    assert output.json['err_desc'] == 'Invalid signature.'


def test_get_user_from_api_query_string_error_success_sha256(pub, local_user):
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    query = (
        'format=json&orig=coucou&algo=sha256&email='
        + urllib.parse.quote(local_user.email)
        + '&timestamp='
        + timestamp
    )
    signature = urllib.parse.quote(
        base64.b64encode(hmac.new(b'1234', force_bytes(query), hashlib.sha256).digest())
    )
    output = get_app(pub).get('/api/user/?%s&signature=%s' % (query, signature))
    assert output.json['user_display_name'] == 'Jean Darmette'


def test_sign_url(pub, local_user):
    signed_url = sign_url(
        'http://example.net/api/user/?format=json&orig=coucou&email=%s'
        % urllib.parse.quote(local_user.email),
        '1234',
    )
    url = signed_url[len('http://example.net') :]
    output = get_app(pub).get(url)
    assert output.json['user_display_name'] == 'Jean Darmette'

    # try to add something after signed url
    get_app(pub).get('%s&foo=bar' % url, status=403)

    signed_url = sign_url(
        'http://example.net/api/user/?format=json&orig=coucou&email=%s'
        % urllib.parse.quote(local_user.email),
        '12345',
    )
    url = signed_url[len('http://example.net') :]
    output = get_app(pub).get(url, status=403)


def test_get_user(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.store()
    local_user.roles = [role.id]
    local_user.store()
    signed_url = sign_url(
        'http://example.net/api/user/?format=json&orig=coucou&email=%s'
        % urllib.parse.quote(local_user.email),
        '1234',
    )
    url = signed_url[len('http://example.net') :]
    output = get_app(pub).get(url)
    assert output.json['user_display_name'] == 'Jean Darmette'
    assert [x['name'] for x in output.json['user_roles']] == ['Foo bar']
    assert [x['slug'] for x in output.json['user_roles']] == ['foo-bar']


def test_api_access_from_xml_storable_object(pub, local_user, admin_user):
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/api-access/new')
    resp.form['name'] = 'Salut API access key'
    resp.form['access_identifier'] = 'salut'
    resp.form['access_key'] = '5678'
    resp = resp.form.submit('submit')

    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.store()
    local_user.roles = [role.id]
    local_user.store()
    signed_url = sign_url(
        'http://example.net/api/user/?format=json&orig=UNKNOWN_ACCESS&email=%s'
        % (urllib.parse.quote(local_user.email)),
        '5678',
    )
    url = signed_url[len('http://example.net') :]
    output = get_app(pub).get(url, status=403)
    assert output.json['err_desc'] == 'Invalid orig.'

    signed_url = sign_url(
        'http://example.net/api/user/?format=json&orig=salut&email=%s'
        % (urllib.parse.quote(local_user.email)),
        '5678',
    )
    url = signed_url[len('http://example.net') :]
    output = get_app(pub).get(url)
    assert output.json['user_display_name'] == 'Jean Darmette'


def test_is_url_signed_check_nonce(pub, local_user, freezer):
    ORIG = 'xxx'
    KEY = 'xxx'

    pub.site_options.add_section('api-secrets')
    pub.site_options.set('api-secrets', ORIG, KEY)
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.token_class.wipe()
    signed_url = sign_url('?format=json&orig=%s&email=%s' % (ORIG, urllib.parse.quote(local_user.email)), KEY)
    req = HTTPRequest(
        None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net', 'QUERY_STRING': signed_url[1:]}
    )
    req.process_inputs()
    pub.set_app_dir(req)
    pub._set_request(req)

    assert is_url_signed()
    with pytest.raises(AccessForbiddenError) as exc_info:
        req.signed = False
        is_url_signed()
    assert exc_info.value.public_msg == 'Nonce already used.'
    # test that clean nonces works
    pub.token_class.clean()
    assert pub.token_class.exists()

    # 20 seconds in the future, nothing should be cleaned
    freezer.move_to(datetime.timedelta(seconds=20))
    pub.token_class.clean()
    assert pub.token_class.exists()

    # 40 seconds in the future, nonces should be removed
    freezer.move_to(datetime.timedelta(seconds=20))
    pub.token_class.clean()
    assert not pub.token_class.exists()


def test_get_user_compat_endpoint(pub, local_user):
    signed_url = sign_url(
        'http://example.net/user?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
        '1234',
    )
    url = signed_url[len('http://example.net') :]
    output = get_app(pub).get(url)
    assert output.json['user_display_name'] == 'Jean Darmette'


def test_apiaccess_xml_to_sql(pub):
    from wcs.qommon.xml_storage import XmlStorableObject
    from wcs.sql import ApiAccess

    # class mixing in XmlStorableObject so all works together
    class OldApiAccessXml(XmlStorableObject, wcs.api_access.ApiAccess):
        # declarations for serialization
        XML_NODES = wcs.api_access.ApiAccess.XML_NODES

    ApiAccess.wipe()
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.store()
    old_api_access = OldApiAccessXml()
    old_api_access.name = 'John doe'
    old_api_access.access_key = '12345'
    old_api_access.access_identifier = '737373737373'
    old_api_access.roles = [role]
    old_api_access.store()

    assert len(ApiAccess.select()) == 0
    ApiAccess.migrate_from_files()
    assert len(ApiAccess.select()) == 1
    new_api_access = ApiAccess.select()[0]
    for field in ('name', 'access_key', 'access_identifier', 'roles'):
        assert getattr(new_api_access, field) == getattr(old_api_access, field)
    assert not (os.path.exists(old_api_access.get_object_filename()))
