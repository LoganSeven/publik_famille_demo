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

import json
import random
import secrets
import tempfile
import uuid
import warnings

import py
import pytest
import responses
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.utils.text import slugify
from jwcrypto.jwk import JWK, JWKSet

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.utils import crypto
from authentic2_auth_oidc.models import OIDCAccount, OIDCClaimMapping, OIDCProvider
from authentic2_auth_oidc.utils import get_openid_configuration_url
from tests.utils import call_command, check_log

User = get_user_model()


def test_args_oidc_register_issuer_command(db, jwkset):

    oidc_conf_f = py.path.local(__file__).dirpath('openid_configuration.json')
    with oidc_conf_f.open() as f:
        oidc_conf = json.load(f)

    issuer_name = 'test issuer'
    issuer = oidc_conf['issuer']
    client_id = secrets.token_hex()
    client_secret = secrets.token_hex()

    cmd_args = [
        'oidc-register-issuer',
        issuer_name,
        '--issuer',
        issuer,
        '--client-secret',
        client_secret,
        '--claim-mapping',
        'courriel email verified required',
    ]
    with pytest.raises(CommandError) as expt:
        call_command(*cmd_args)
    assert expt.value.args[0] == 'Client identifier must be specified'

    cmd_args = [
        'oidc-register-issuer',
        issuer_name,
        '--issuer',
        issuer,
        '--client-id',
        client_id,
        '--claim-mapping',
        'courriel email verified required',
    ]
    with pytest.raises(CommandError) as expt:
        call_command(*cmd_args)
    assert expt.value.args[0] == 'Client secret must be specified'

    cmd_args = [
        'oidc-register-issuer',
        issuer_name,
        '--client-id',
        client_id,
        '--client-secret',
        client_secret,
        '--claim-mapping',
        'courriel email verified required',
    ]
    with pytest.raises(CommandError) as expt:
        with pytest.warns(FutureWarning) as warns:
            call_command(*cmd_args)
    assert len(warns) == 2
    assert warns[0].message.args[0] == '--client-id given but will not be used'
    assert warns[1].message.args[0] == '--client-secret given but will not be used'
    assert expt.value.args[0] == 'Unknown OIDC provider'

    cmd_args = [
        'oidc-register-issuer',
        issuer_name,
        '--claim-mapping',
        'courriel email verified required',
    ]
    with pytest.raises(CommandError) as expt:
        with warnings.catch_warnings():
            call_command(*cmd_args)
    assert expt.value.args[0] == 'Unknown OIDC provider'


@pytest.mark.parametrize('get_config', (True, False))
@pytest.mark.parametrize('end_session,token_revocation', [(True, True), (True, False), (False, True)])
@responses.activate
def test_oidc_register_issuer_command(db, jwkset, get_config, end_session, token_revocation):

    oidc_conf_f = py.path.local(__file__).dirpath('openid_configuration.json')
    with oidc_conf_f.open() as f:
        oidc_conf = json.load(f)

    client_id = secrets.token_hex()
    client_secret = secrets.token_hex()
    name = 'test issuer !'
    slug = slugify(name)
    jwkset_data = jwkset.export(as_dict=True)

    with tempfile.NamedTemporaryFile(buffering=0) as tmpconf:
        if not end_session:
            del oidc_conf['end_session_endpoint']
        if not token_revocation:
            del oidc_conf['token_revocation_endpoint']

        tmpconf.write(json.dumps(oidc_conf).encode())

        issuer = oidc_conf['issuer']

        cmd_args = [
            'oidc-register-issuer',
            name,
            '--issuer',
            issuer,
            '--client-id',
            client_id,
            '--client-secret',
            client_secret,
            '--claim-mapping',
            'courriel email verified required',
        ]

        with responses.RequestsMock() as rsps:

            if get_config:
                issuer_url = get_openid_configuration_url(issuer)
                rsps.get(issuer_url, json=oidc_conf)
            else:
                cmd_args += ['--openid-configuration', tmpconf.name]

            rsps.get(oidc_conf['jwks_uri'], json=jwkset_data)

            call_command(*cmd_args)

            provider = OIDCProvider.objects.get(name=name)
            assert provider.issuer == issuer
            assert provider.name == name
            assert provider.slug == slug
            assert provider.client_id == client_id
            assert provider.client_secret == client_secret
            assert provider.jwkset_url == oidc_conf['jwks_uri']
            assert provider.userinfo_endpoint == oidc_conf['userinfo_endpoint']
            assert provider.token_endpoint == oidc_conf['token_endpoint']
            assert provider.authorization_endpoint == oidc_conf['authorization_endpoint']
            assert sorted(
                [key for key in provider.jwkset_json['keys']], key=lambda elt: len(json.dumps(elt))
            ) == sorted([key for key in jwkset_data['keys']], key=lambda elt: len(json.dumps(elt)))
            if end_session:
                assert provider.end_session_endpoint == oidc_conf['end_session_endpoint']
            else:
                assert provider.end_session_endpoint is None
            if token_revocation:
                assert provider.token_revocation_endpoint == oidc_conf['token_revocation_endpoint']
            else:
                assert provider.token_revocation_endpoint is None
            assert provider.ou == get_default_ou()

            claims = OIDCClaimMapping.objects.filter(authenticator=provider)
            assert claims.count() == 1
            claim = claims[0]
            assert claim.claim == 'courriel'
            assert claim.attribute == 'email'
            assert claim.required
            assert claim.verified == OIDCClaimMapping.VERIFIED_CLAIM
            assert not claim.idtoken_claim


@pytest.mark.parametrize(
    'verified',
    [
        OIDCClaimMapping.VERIFIED_CLAIM,
        OIDCClaimMapping.ALWAYS_VERIFIED,
        OIDCClaimMapping.NOT_VERIFIED,
    ],
)
@pytest.mark.parametrize(
    'required,idtoken_claim', [(True, True), (True, False), (False, True), (False, False)]
)
@pytest.mark.parametrize(
    'claim_name,attr',
    [
        ('courriel', 'email'),
        ('email', 'email'),
    ],
)
def test_oidc_register_issuer_add_claim_mappings(
    db, jwkset, claim_name, attr, required, idtoken_claim, verified
):
    oidc_provider = OIDCProvider.objects.create(
        issuer='https://some.provider',
        authorization_endpoint='https://some.provider/authorizations',
        token_endpoint='https://some.provider/token',
        userinfo_endpoint='https://some.provider/userinfo',
        strategy=OIDCProvider.STRATEGY_CREATE,
        jwkset_json=jwkset.export(as_dict=True),
        name='Some Provider',
        slug='some-provider',
        client_id='someid',
        client_secret='supersecret',
        ou=get_default_ou(),
    )

    claim_args = []
    if verified == OIDCClaimMapping.VERIFIED_CLAIM:
        claim_args.append('verified')
    elif verified == OIDCClaimMapping.ALWAYS_VERIFIED:
        claim_args.append('always_verified')
    if required:
        claim_args.append('required')
    if idtoken_claim:
        claim_args.append('idtoken')

    random.shuffle(claim_args)

    claim_arg = '%s %s %s' % (claim_name, attr, ' '.join(claim_args))

    cmd_args = [
        'oidc-register-issuer',
        oidc_provider.name,
        '--claim-mapping',
        claim_arg,
        '--claim-mapping',
        'pseudo username verified required',
    ]

    call_command(*cmd_args)
    claim = OIDCClaimMapping.objects.get(authenticator=oidc_provider, claim=claim_name)
    assert claim.claim == claim_name
    assert claim.attribute == attr
    assert claim.verified == verified
    assert claim.required == required
    assert claim.idtoken_claim == idtoken_claim

    oidc_provider.refresh_from_db()
    assert oidc_provider.client_secret == 'supersecret'  # bug
    assert oidc_provider.client_id == 'someid'  # bug


@pytest.mark.parametrize('to_delete', [('not_existing',), ('email',), ('email', 'username', 'family_name')])
def test_oidc_register_issuer_delete_claim_mappings(db, jwkset, to_delete):
    oidc_provider = OIDCProvider.objects.create(
        issuer='https://some.provider',
        authorization_endpoint='https://some.provider/authorizations',
        token_endpoint='https://some.provider/token',
        userinfo_endpoint='https://some.provider/userinfo',
        strategy=OIDCProvider.STRATEGY_CREATE,
        jwkset_json=jwkset.export(as_dict=True),
        name='Some Provider',
        slug='some-provider',
        client_id='someid',
        client_secret='supersecret',
        ou=get_default_ou(),
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='username',
        idtoken_claim=False,
        claim='username',
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='email',
        idtoken_claim=False,
        claim='email',
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='last_name',
        idtoken_claim=True,
        claim='family_name',
    )

    cmd_args = [
        'oidc-register-issuer',
        oidc_provider.name,
    ]
    for claim in to_delete:
        cmd_args += ['--delete-claim', claim]

    call_command(*cmd_args)

    all_claims = ['username', 'email', 'family_name']
    claims_left = set(all_claims) - set(to_delete)

    for claim in all_claims:
        qs = OIDCClaimMapping.objects.filter(authenticator=oidc_provider, claim=claim)
        if claim in claims_left:
            assert qs.count() == 1
        else:
            assert not qs.exists()


@responses.activate
@pytest.mark.parametrize('deletion_number,deletion_valid', [(2, True), (5, True), (10, False)])
def test_oidc_sync_provider(
    db, app, admin, settings, caplog, deletion_number, deletion_valid, nologtoconsole
):
    oidc_provider = OIDCProvider.objects.create(
        issuer='https://some.provider',
        name='Some Provider',
        slug='some-provider',
        ou=get_default_ou(),
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='username',
        idtoken_claim=False,
        claim='username',
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='email',
        idtoken_claim=False,
        claim='email',
    )
    # last one, with an idtoken claim
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='last_name',
        idtoken_claim=True,
        claim='family_name',
    )
    # typo in template string
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='first_name',
        idtoken_claim=True,
        claim='given_name',
    )
    User = get_user_model()
    for i in range(100):
        user = User.objects.create(
            first_name='John%s' % i,
            last_name='Doe%s' % i,
            username='john.doe.%s' % i,
            email='john.doe.%s@ad.dre.ss' % i,
            ou=get_default_ou(),
        )
        identifier = uuid.UUID(user.uuid).bytes
        sector_identifier = 'some.provider'
        cipher_args = [
            settings.SECRET_KEY.encode('utf-8'),
            identifier,
            sector_identifier,
        ]
        sub = crypto.aes_base64url_deterministic_encrypt(*cipher_args).decode('utf-8')
        OIDCAccount.objects.create(user=user, provider=oidc_provider, sub=sub)

    to_modify = random.sample(list(User.objects.filter(username__startswith='john.doe').all()), 20)
    to_delete = random.sample(list(OIDCAccount.objects.all()), deletion_number)
    modify_count = len({u.pk for u in to_modify})
    if deletion_valid:
        modify_count -= len({u.pk for u in to_modify} & {a.user.pk for a in to_delete})

    def synchronization_get_modified_response():
        # randomized batch of modified users
        results = []
        for count, user in enumerate(to_modify):
            user_json = user.to_json()
            user_json['username'] = f'modified_{count}'
            user_json['first_name'] = 'Mod'
            user_json['last_name'] = 'Ified'
            # mocking claim resolution by oidc provider
            user_json['given_name'] = 'Mod'
            user_json['family_name'] = 'Ified'

            # add user sub to response
            try:
                account = OIDCAccount.objects.get(user=user)
            except OIDCAccount.DoesNotExist:
                raise RuntimeError('Should not happen')
            else:
                user_json['sub'] = account.sub

            results.append(user_json)
        return {'results': results}

    responses.post(
        'https://some.provider/api/users/synchronization/',
        json={'unknown_uuids': [account.sub for account in to_delete]},
    )
    responses.get('https://some.provider/api/users/', json=synchronization_get_modified_response())

    with check_log(caplog, 'no provider supporting synchronization'):
        call_command('oidc-sync-provider', '-v1')

    oidc_provider.a2_synchronization_supported = True
    oidc_provider.save()

    with check_log(caplog, 'no provider supporting synchronization'):
        call_command('oidc-sync-provider', '--provider', 'whatever', '-v1')

    with check_log(caplog, 'got 20 users'):
        call_command('oidc-sync-provider', '-v1')
    if deletion_valid:
        # existing users check
        assert OIDCAccount.objects.count() == 100 - deletion_number
    else:
        assert OIDCAccount.objects.count() == 100
        assert caplog.records[3].levelname == 'ERROR'
        assert 'deletion ratio is abnormally high' in caplog.records[3].message

    # users update
    assert User.objects.filter(username__startswith='modified').count() == modify_count
    assert User.objects.filter(first_name='Mod', last_name='Ified').count() == modify_count


@responses.activate
def test_auth_oidc_refresh_jwkset_json(db, app, admin, settings, caplog):
    jwkset_url = 'https://www.example.com/common/discovery/v3.0/keys'
    kid_rsa = '123'
    kid_ec = '456'

    def generate_remote_jwkset_json():
        key_rsa = JWK.generate(kty='RSA', size=1024, kid=kid_rsa)
        key_ec = JWK.generate(kty='EC', size=256, kid=kid_ec)
        jwkset = JWKSet()
        jwkset.add(key_rsa)
        jwkset.add(key_ec)
        d = jwkset.export(as_dict=True)
        # add extra key without kid to check it is just ignored by change logging
        other_key = JWK.generate(kty='EC', size=256).export(as_dict=True)
        other_key.pop('kid', None)
        d['keys'].append(other_key)
        return d

    responses.get(
        jwkset_url,
        json={
            'headers': {
                'content-type': 'application/json',
            },
            'status_code': 200,
            **generate_remote_jwkset_json(),
        },
    )

    issuer = ('https://www.example.com',)
    provider = OIDCProvider(
        ou=get_default_ou(),
        name='Foo',
        slug='foo',
        client_id='abc',
        client_secret='def',
        enabled=True,
        issuer=issuer,
        authorization_endpoint='%s/authorize' % issuer,
        token_endpoint='%s/token' % issuer,
        end_session_endpoint='%s/logout' % issuer,
        userinfo_endpoint='%s/user_info' % issuer,
        token_revocation_endpoint='%s/revoke' % issuer,
        jwkset_url=jwkset_url,
        idtoken_algo=OIDCProvider.ALGO_RSA,
        claims_parameter_supported=False,
        button_label='Connect with Foo',
        strategy=OIDCProvider.STRATEGY_CREATE,
    )
    provider.full_clean()
    provider.save()
    assert {key.get('kid') for key in provider.jwkset_json['keys']} == {'123', '456', None}

    kid_rsa = 'abcdefg'
    kid_ec = 'hijklmn'

    responses.replace(
        responses.GET,
        jwkset_url,
        json={
            'headers': {
                'content-type': 'application/json',
            },
            'status_code': 200,
            **generate_remote_jwkset_json(),
        },
    )

    call_command('oidc-refresh-jwkset-json', '-v1')
    provider.refresh_from_db()
    assert {key.get('kid') for key in provider.jwkset_json['keys']} == {'abcdefg', 'hijklmn', None}
