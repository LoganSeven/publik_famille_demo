import os

import pytest
from quixote import get_request

from wcs import fields
from wcs.carddef import CardDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub._set_request(req)
    pub.cfg['users'] = {
        'field_phone': '_phone',
    }
    pub.write_cfg()

    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [fields.StringField(id='_phone', label='phone', validation={'type': 'phone'})]
    formdef.store()

    return pub


@pytest.fixture
def user(pub, request):
    pub.user_class.wipe()
    user = pub.user_class(name='user')
    user.email = 'test@example.net'
    user.form_data = {'_phone': '+33123456789'}
    user.store()
    get_request()._user = user
    return user


def teardown_module(module):
    clean_temporary_pub()


def test_prefill_string(pub):
    field = fields.Field()
    field.prefill = {'type': 'string', 'value': 'test'}
    assert field.get_prefill_value() == ('test', False)


def test_prefill_string_carddef(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.store()

    carddata_class = carddef.data_class()
    carddata_class.wipe()
    carddata = carddata_class()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()

    field = fields.Field()
    field.prefill = {'type': 'string', 'value': '{{cards|objects:"foo"|first|get:"foo"}}'}
    assert field.get_prefill_value() == ('hello world', False)

    LoggedError.wipe()
    field.prefill = {'type': 'string', 'value': '{{cards|objects:"unknown"|first|get:"foo"}}'}
    assert field.get_prefill_value() == ('None', False)
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == '|objects with invalid reference (\'unknown\')'


def test_prefill_user_email(user):
    field = fields.Field()
    field.prefill = {'type': 'user', 'value': 'email'}
    assert field.get_prefill_value(user=get_request().user) == ('test@example.net', False)


def test_prefill_user_phone(user):
    field = fields.Field()
    field.prefill = {'type': 'user', 'value': 'phone'}
    assert field.get_prefill_value(user=get_request().user) == ('01 23 45 67 89', False)


def test_prefill_user_phone_fr_validation(pub, user):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'local-region-code', 'FR')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    field = fields.Field()
    field.validation = {'type': 'phone-fr'}
    field.prefill = {'type': 'user', 'value': 'phone'}
    assert field.get_prefill_value(user=get_request().user) == ('01 23 45 67 89', False)


def test_prefill_user_phone_validation(pub, user):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'local-region-code', 'BE')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    field = fields.Field()
    field.validation = {'type': 'phone'}
    field.prefill = {'type': 'user', 'value': 'phone'}
    assert field.get_prefill_value(user=get_request().user) == ('01 23 45 67 89', False)

    user = get_request().user
    user.form_data['_phone'] = '+3281000000'
    user.store()

    assert field.get_prefill_value(user=get_request().user) == ('081 00 00 00', False)

    user.form_data['_phone'] = '+99981000000'
    user.store()

    assert field.get_prefill_value(user=get_request().user) == ('+99981000000', False)


def test_prefill_user_other_phone(pub, user):
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='_phone', label='phone', validation={'type': 'phone'}),
        fields.StringField(id='_mobile', label='mobile', validation={'type': 'phone'}),
    ]
    formdef.store()

    user.form_data['_mobile'] = '+33123456780'
    user.store()

    field = fields.Field()
    field.prefill = {'type': 'user', 'value': '_mobile'}
    assert field.get_prefill_value(user=get_request().user) == ('01 23 45 67 80', False)


def test_prefill_user_attribute(pub, user):
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [fields.StringField(id='3', label='test', varname='plop')]
    formdef.store()

    field = fields.Field()
    field.prefill = {'type': 'user', 'value': '3'}
    assert field.get_prefill_value(user=get_request().user) == (None, False)

    user.form_data = {'3': 'Plop'}
    user.store()
    assert field.get_prefill_value(user=get_request().user) == ('Plop', False)


def test_prefill_verified_user_attribute(pub, user):
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [fields.StringField(id='3', label='test', varname='plop')]
    formdef.store()

    field = fields.Field()
    field.prefill = {'type': 'user', 'value': '3'}
    assert field.get_prefill_value(user=get_request().user) == (None, False)

    user.form_data = {'3': 'Plop'}
    user.verified_fields = ['3']
    user.store()
    assert field.get_prefill_value(user=get_request().user) == ('Plop', True)
