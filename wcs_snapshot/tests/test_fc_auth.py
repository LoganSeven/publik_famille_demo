import base64
import json
import urllib.parse

import responses
from django.utils.encoding import force_bytes, force_str
from quixote import cleanup, get_publisher, get_session_manager

from wcs.logged_errors import LoggedError

from .utilities import create_temporary_pub, get_app

PROFILE = {
    'fields': [
        {
            'kind': 'string',
            'description': '',
            'required': True,
            'user_visible': True,
            'label': 'Prenoms',
            'disabled': False,
            'user_editable': True,
            'asked_on_registration': True,
            'name': 'prenoms',
        },
        {
            'kind': 'string',
            'description': '',
            'required': True,
            'user_visible': True,
            'label': 'Nom',
            'disabled': False,
            'user_editable': True,
            'asked_on_registration': True,
            'name': 'nom',
        },
        {
            'kind': 'string',
            'description': '',
            'required': True,
            'user_visible': True,
            'label': 'Email',
            'disabled': False,
            'user_editable': True,
            'asked_on_registration': True,
            'name': 'email',
        },
    ]
}


def base64url_encode(v):
    return base64.urlsafe_b64encode(force_bytes(v)).strip(b'=')


def setup_module(module):
    cleanup()
    global pub
    pub = create_temporary_pub()


def setup_user_profile(pub):
    if not pub.cfg:
        pub.cfg = {}
    # create some roles
    from wcs.ctl.management.commands.hobo_deploy import Command

    # setup an hobo profile
    Command().update_profile(PROFILE, pub)
    pub.cfg['users']['fullname_template'] = '{{ user_var_prenoms }} {{ user_var_nom }}'
    pub.user_class.wipe()
    pub.write_cfg()


FC_CONFIG = {
    'client_id': '123',
    'client_secret': 'xyz',
    'platform': 'dev-particulier',
    'scopes': 'identite_pivot',
    'user_field_mappings': [
        {
            'field_varname': 'prenoms',
            'value': '{{ given_name|default:"" }}',
            'verified': 'always',
        },
        {
            'field_varname': 'nom',
            'value': '{{ family_name|default:"" }}',
            'verified': 'always',
        },
        {
            'field_varname': 'email',
            'value': '{{ email|default:"" }}',
            'verified': 'always',
        },
    ],
}


def setup_fc_environment(pub):
    if not pub.cfg:
        pub.cfg = {}
    pub.cfg['identification'] = {
        'methods': ['fc'],
    }
    pub.cfg['fc'] = FC_CONFIG
    pub.user_class.wipe()
    pub.write_cfg()


def get_session(app):
    pub = get_publisher()
    try:
        session_id = app.cookies[pub.config.session_cookie_name]
    except KeyError:
        return None
    session_id = session_id.strip('"')
    return get_session_manager().session_class.get(session_id)


def test_fc_login_page(caplog):
    setup_user_profile(pub)
    setup_fc_environment(pub)
    app = get_app(pub)
    resp = app.get('/')
    resp = app.get('/login/')
    assert resp.status_int == 302
    assert resp.location.startswith('https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize')
    qs = urllib.parse.parse_qs(resp.location.split('?')[1])
    nonce = qs['nonce'][0]
    state = qs['state'][0]

    id_token = {
        'nonce': nonce,
    }
    token_result = {
        'access_token': 'abcd',
        'id_token': '.%s.' % force_str(base64url_encode(json.dumps(id_token))),
    }
    user_info_result = {
        'sub': 'ymca',
        'given_name': 'John',
        'family_name': 'Doe',
        'email': 'john.doe@example.com',
    }

    assert pub.user_class.count() == 0
    with responses.RequestsMock() as rsps:
        rsps.post('https://fcp.integ01.dev-franceconnect.fr/api/v1/token', json=token_result)
        rsps.get('https://fcp.integ01.dev-franceconnect.fr/api/v1/userinfo', json=user_info_result)
        resp = app.get(
            '/ident/fc/callback?%s'
            % urllib.parse.urlencode(
                {
                    'code': '1234',
                    'state': state,
                }
            )
        )
    assert pub.user_class.count() == 1
    user = pub.user_class.select()[0]
    assert user.form_data == {'_email': 'john.doe@example.com', '_nom': 'Doe', '_prenoms': 'John'}
    assert set(user.verified_fields) == {'_nom', '_prenoms', '_email'}
    assert user.email == 'john.doe@example.com'
    assert user.name_identifiers == ['ymca']
    assert user.name == 'John Doe'

    # Verify we are logged in
    session = get_session(app)
    assert session.user == user.id
    assert session.extra_user_variables['fc_given_name'] == 'John'
    assert session.extra_user_variables['fc_family_name'] == 'Doe'
    assert session.extra_user_variables['fc_email'] == 'john.doe@example.com'
    assert session.extra_user_variables['fc_sub'] == 'ymca'

    resp = app.get('/logout')
    splitted = urllib.parse.urlsplit(resp.location)
    assert (
        urllib.parse.urlunsplit((splitted.scheme, splitted.netloc, splitted.path, '', ''))
        == 'https://fcp.integ01.dev-franceconnect.fr/api/v1/logout'
    )
    assert urllib.parse.parse_qs(splitted.query)['post_logout_redirect_uri'] == ['http://example.net']
    assert urllib.parse.parse_qs(splitted.query)['id_token_hint']
    assert not get_session(app)

    # Test error handling path
    resp = app.get(
        '/ident/fc/callback?%s'
        % urllib.parse.urlencode(
            {
                'state': state,
                'error': 'access_denied',
            }
        )
    )
    assert 'user did not authorize login' in LoggedError.select(order_by='id')[-1].summary
    resp = app.get(
        '/ident/fc/callback?%s'
        % urllib.parse.urlencode(
            {
                'state': state,
                'error': 'whatever',
            }
        )
    )
    assert 'whatever' in LoggedError.select(order_by='id')[-1].summary

    # Login existing user
    def logme(login_url):
        resp = app.get(login_url)
        assert resp.status_int == 302
        assert resp.location.startswith('https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize')
        qs = urllib.parse.parse_qs(resp.location.split('?')[1])
        state = qs['state'][0]
        id_token['nonce'] = qs['nonce'][0]
        token_result['id_token'] = '.%s.' % force_str(base64url_encode(json.dumps(id_token)))

        with responses.RequestsMock() as rsps:
            rsps.post('https://fcp.integ01.dev-franceconnect.fr/api/v1/token', json=token_result)
            rsps.get('https://fcp.integ01.dev-franceconnect.fr/api/v1/userinfo', json=user_info_result)
            resp = app.get(
                '/ident/fc/callback?%s'
                % urllib.parse.urlencode(
                    {
                        'code': '1234',
                        'state': state,
                    }
                )
            )
        return resp

    app.get('/logout')
    resp = logme('/login/')
    new_session = get_session(app)
    assert session.id != new_session.id, 'no new session created'
    assert pub.user_class.count() == 1, 'existing user has not been used'
    assert new_session.user == user.id

    # Login with next url
    app.get('/logout')
    resp = logme('/login/?next=/foo/bar/')
    assert resp.status_int == 302
    assert resp.location.endswith('/foo/bar/')

    # Direct login link
    app.get('/logout')
    resp = logme('/ident/fc/login')
    new_session = get_session(app)
    assert session.id != new_session.id, 'no new session created'
    assert pub.user_class.count() == 1, 'existing user has not been used'
    assert new_session.user == user.id
    app.get('/logout')
    resp = logme('/ident/fc/login?next=/foo/bar/')
    assert resp.status_int == 302
    assert resp.location.endswith('/foo/bar/')

    # User with missing attributes
    resp = app.get('/logout')
    resp = app.get('/login/')
    assert resp.status_int == 302
    assert resp.location.startswith('https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize')
    qs = urllib.parse.parse_qs(resp.location.split('?')[1])
    state = qs['state'][0]
    id_token['nonce'] = qs['nonce'][0]
    token_result['id_token'] = '.%s.' % force_str(base64url_encode(json.dumps(id_token)))
    bad_user_info_result = {
        'sub': 'ymca2',
        'given_name': 'John',
        'family_name': 'Deux',
        # 'email': 'john.deux@example.com',  # missing
    }
    with responses.RequestsMock() as rsps:
        rsps.post('https://fcp.integ01.dev-franceconnect.fr/api/v1/token', json=token_result)
        rsps.get('https://fcp.integ01.dev-franceconnect.fr/api/v1/userinfo', json=bad_user_info_result)

        resp = app.get(
            '/ident/fc/callback?%s'
            % urllib.parse.urlencode(
                {
                    'code': '1234',
                    'state': state,
                }
            )
        )
    assert pub.user_class.count() == 1, 'an invalid user (no email) has been created'
    session = get_session(app)
    assert not session or not session.user


def test_fc_settings():
    setup_user_profile(pub)
    app = get_app(pub)
    resp = app.get('/backoffice/settings/identification/')
    resp.forms[0]['methods$elementfc'].checked = True
    resp = resp.forms[0].submit().follow()

    assert 'FranceConnect' in resp.text
    resp = resp.click('FranceConnect')
    resp = resp.forms[0].submit('user_field_mappings$add_element')
    resp = resp.forms[0].submit('user_field_mappings$add_element')
    resp.forms[0]['client_id'].value = '123'
    resp.forms[0]['client_secret'].value = 'xyz'
    resp.forms[0]['platform'].value = 'Development citizens'
    resp.forms[0]['scopes'].value = 'identite_pivot'

    resp.forms[0]['user_field_mappings$element0$field_varname'] = 'prenoms'
    resp.forms[0]['user_field_mappings$element0$value$value_template'] = '{{ given_name|default:"" }}'
    resp.forms[0]['user_field_mappings$element0$verified'] = 'Always'

    resp.forms[0]['user_field_mappings$element1$field_varname'] = 'nom'
    resp.forms[0]['user_field_mappings$element1$value$value_template'] = '{{ family_name|default:"" }}'
    resp.forms[0]['user_field_mappings$element1$verified'] = 'Always'

    resp.forms[0]['user_field_mappings$element2$field_varname'] = 'email'
    resp.forms[0]['user_field_mappings$element2$value$value_template'] = '{{ email|default:"" }}'
    resp.forms[0]['user_field_mappings$element2$verified'] = 'Always'

    resp = resp.forms[0].submit('submit').follow()
    assert pub.cfg['fc'] == FC_CONFIG


def test_fc_settings_no_user_profile():
    FC_CONFIG = {
        'client_id': '123',
        'client_secret': 'xyz',
        'platform': 'dev-particulier',
        'scopes': 'identite_pivot',
        'user_field_mappings': [
            {
                'field_varname': '__name',
                'value': '{{ given_name|default:"" }} {{ family_name|default:"" }}',
                'verified': 'always',
            },
            {
                'field_varname': '__email',
                'value': '{{ email|default:"" }}',
                'verified': 'always',
            },
        ],
    }

    for k in list(pub.cfg.keys()):
        if k not in ('misc', 'postgresql'):
            del pub.cfg[k]
    pub.user_class.wipe()
    pub.write_cfg()
    app = get_app(pub)
    resp = app.get('/backoffice/settings/identification/')
    resp.forms[0]['methods$elementfc'].checked = True
    resp = resp.forms[0].submit().follow()

    assert 'FranceConnect' in resp.text
    resp = resp.click('FranceConnect')
    resp = resp.forms[0].submit('user_field_mappings$add_element')
    resp = resp.forms[0].submit('user_field_mappings$add_element')
    resp.forms[0]['client_id'].value = '123'
    resp.forms[0]['client_secret'].value = 'xyz'
    resp.forms[0]['platform'].value = 'Development citizens'
    resp.forms[0]['scopes'].value = 'identite_pivot'

    resp.forms[0]['user_field_mappings$element0$field_varname'] = '__name'
    resp.forms[0][
        'user_field_mappings$element0$value$value_template'
    ] = '{{ given_name|default:"" }} {{ family_name|default:"" }}'
    resp.forms[0]['user_field_mappings$element0$verified'] = 'Always'

    resp.forms[0]['user_field_mappings$element2$field_varname'] = '__email'
    resp.forms[0]['user_field_mappings$element2$value$value_template'] = '{{ email|default:"" }}'
    resp.forms[0]['user_field_mappings$element2$verified'] = 'Always'

    resp = resp.forms[0].submit('submit').follow()
    assert pub.cfg['fc'] == FC_CONFIG


def test_fc_logout_error():
    setup_user_profile(pub)
    setup_fc_environment(pub)
    app = get_app(pub)
    app.get('/ident/fc/logout', status=400)


def test_fc_register_error():
    setup_user_profile(pub)
    setup_fc_environment(pub)
    app = get_app(pub)
    app.get('/register/fc/', status=404)
