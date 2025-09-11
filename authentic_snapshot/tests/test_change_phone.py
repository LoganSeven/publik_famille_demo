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

from unittest import mock

import pytest

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import Attribute, SMSCode, Token

from .utils import login

pytestmark = pytest.mark.django_db


def test_change_phone(app, phone_user):
    Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    phone_user.attributes.phone = '+33122446688'
    phone_user.attributes.another_phone = '+33122444444'
    phone_user.phone = ''
    phone_user.phone_verified_on = None
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    assert 'Your current phone number is +33122446688.' in resp.text
    assert 'Change Phone attribute used for authentication' in resp.pyquery('title')[0].text

    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446666'
    assert phone_user.attributes.another_phone == '+33122444444'  # unchanged
    assert phone_user.phone_verified_on is not None


def test_change_phone_no_password(app, phone_user):
    Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    phone_user.attributes.phone = '+33122446688'
    phone_user.attributes.another_phone = '+33122444444'
    phone_user.phone = ''
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )
    with mock.patch(
        'authentic2.views.IdentifierChangeMixin.can_validate_with_password'
    ) as mocked_can_validate:
        with mock.patch(
            'authentic2.views.IdentifierChangeMixin.has_recent_authentication'
        ) as mocked_has_recent_authn:
            mocked_can_validate.return_value = False
            mocked_has_recent_authn.return_value = True
            resp = app.get('/accounts/change-phone/')
            assert 'Your current phone number is +33122446688.' in resp.text
            resp.form.set('phone_1', '122446666')
            assert 'password' not in resp.form.fields
            resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446666'
    assert phone_user.attributes.another_phone == '+33122444444'  # unchanged
    assert phone_user.phone_verified_on is not None


def test_change_phone_no_password_no_recent_authn(app, phone_user):
    Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    phone_user.attributes.phone = '+33122446688'
    phone_user.attributes.another_phone = '+33122444444'
    phone_user.phone = ''
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )
    with mock.patch(
        'authentic2.views.IdentifierChangeMixin.can_validate_with_password'
    ) as mocked_can_validate:
        with mock.patch(
            'authentic2.views.IdentifierChangeMixin.has_recent_authentication'
        ) as mocked_has_recent_authn:
            mocked_can_validate.return_value = False
            mocked_has_recent_authn.return_value = False
            resp = app.get('/accounts/change-phone/')
            resp = resp.follow()
            assert resp.pyquery('li.info')[0].text == 'You must re-authenticate to change your phone number.'
            resp.form.set('username', phone_user.phone_identifier)
            resp.form.set('password', phone_user.clear_password)
            resp = resp.form.submit(name='login-password-submit')
            mocked_has_recent_authn.return_value = True
            resp = resp.follow().maybe_follow()
            resp.form.set('phone_1', '122446666')
            assert 'Your current phone number is +33122446688.' in resp.text
            assert 'password' not in resp.form.fields
            resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446666'
    assert phone_user.attributes.another_phone == '+33122444444'  # unchanged
    assert phone_user.phone_verified_on is not None


def test_change_phone_wrong_input(app, phone_user):
    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '12244666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Metropolitan France must respect local format (e.g. 06 39 98 01 23).'
    ) == resp.pyquery('.error p')[0].text_content().strip()

    resp.form.set('phone_0', '32')
    resp.form.set('phone_1', '12244')
    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Belgium must respect local format (e.g. 042 11 22 33).'
    ) == resp.pyquery('.error p')[0].text_content().strip()

    assert not SMSCode.objects.count()
    assert not Token.objects.count()
    resp.form.set('phone_1', 'abc')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Belgium must respect local format (e.g. 042 11 22 33).'
    ) == resp.pyquery('.error p')[0].text_content().strip()
    assert not SMSCode.objects.count()
    assert not Token.objects.count()


def test_change_phone_expired_code(freezer, app, phone_user):
    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    freezer.tick(3600)  # user did not immediately submit code
    resp = resp.form.submit('')
    assert resp.pyquery('ul.errorlist li')[0].text == 'The code has expired.'
    assert not Token.objects.count()


def test_change_phone_code_modified(app, phone_user):
    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit()
    location = resp.location[:-5] + 'wxyz/'  # oops, something went wrong with the url token
    app.get(location, status=404)
    assert not Token.objects.count()

    location = (
        resp.location[:-5] + 'abcd/'
    )  # oops, something went wrong again although it's a valid uuid format
    app.get(location, status=404)
    assert not Token.objects.count()


def test_change_phone_token_modified(app, phone_user):
    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('')
    resp.location = resp.location.split('?')[0]
    resp.location = resp.location[:-5] + 'abcd/'  # oops, something went wrong with the url token
    resp = resp.follow().maybe_follow()
    assert resp.pyquery('.error')[0].text == 'Your phone number update request is invalid, try again'
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446688'


def test_change_phone_identifier_attribute_changed(
    app,
    phone_user,
    phone_activated_authn,
):
    phone, dummy = Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    phone_user.attributes.phone = '+33122446688'
    phone_user.attributes.another_phone = '+33122444444'
    phone_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    assert 'Your current phone number is +33122446688.' in resp.text

    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    phone_activated_authn.phone_identifier_field = phone
    phone_activated_authn.save()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    phone_user.refresh_from_db()
    # phone fields have been swapped, nothing really worth doing about this
    # we just check that the phone change request does not crash
    assert phone_user.attributes.phone == '+33122446688'  # unchanged
    assert phone_user.attributes.another_phone == '+33122446666'


def test_change_phone_authn_deactivated(app, phone_user, phone_activated_authn):
    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )

    phone_activated_authn.accept_phone_authentication = False
    phone_activated_authn.save()

    resp = app.get('/accounts/change-phone/')
    assert 'Your current phone number is +33122446688.' in resp.text

    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    # assert not SMSCode.objects.count()  # avoid multiple uses
    phone_user.refresh_from_db()


def test_change_phone_identifier_field_unknown(app, phone_user, phone_activated_authn):
    phone_user.attributes.phone = '+33122446688'

    login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )

    phone_activated_authn.phone_identifier_field = None
    phone_activated_authn.save()

    app.get('/accounts/change-phone/', status=404)


def test_change_phone_identifier_field_not_user_editable(
    app,
    phone_user,
    phone_activated_authn,
):
    phone_user.attributes.phone = '+33122446688'

    login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )

    phone_activated_authn.phone_identifier_field.user_editable = False
    phone_activated_authn.phone_identifier_field.save()

    app.get('/accounts/change-phone/', status=404)


def test_change_phone_identifier_field_disabled(app, phone_user, phone_activated_authn):
    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )

    phone_activated_authn.phone_identifier_field.disabled = True
    phone_activated_authn.phone_identifier_field.save()

    app.get('/accounts/change-phone/', status=404)


def test_phone_change_already_existing(
    app,
    simple_user,
    phone_user,
    settings,
):
    settings.A2_PHONE_IS_UNIQUE = True

    phone_user.attributes.phone = '+33122446688'
    phone_user.save()
    simple_user.attributes.phone = '+33122446666'
    simple_user.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow().maybe_follow()
    assert resp.pyquery('li.error')[0].text == 'This phone number is already used by another account.'


def test_phone_change_preempted_during_request(app, phone_user, settings, simple_user):
    settings.A2_PHONE_IS_UNIQUE = True

    phone_user.attributes.phone = '+33122446688'

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    # oops, some other user took this number during the change request
    simple_user.attributes.phone = '+33122446666'
    simple_user.save()
    resp = resp.form.submit('').follow().maybe_follow()
    assert resp.pyquery('li.error')[0].text == 'This phone number is already used by another account.'


def test_phone_change_lock_identifier_error_token_use(app, phone_user, monkeypatch):
    from authentic2.models import Lock

    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    def erroneous_lock_identifier(identifier, nowait=False):
        raise Lock.Error

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()

    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('')
    monkeypatch.setattr(Lock, 'lock_identifier', erroneous_lock_identifier)
    resp = resp.follow().maybe_follow()
    assert 'Something went wrong while updating' in resp.pyquery('li.error')[0].text
    assert phone_user.attributes.phone == '+33122446688'


def test_phone_change_no_existing_number(app, simple_user, phone_activated_authn):
    resp = login(
        app,
        simple_user,
        path='/accounts/change-phone/',
    )
    assert 'Your account does not declare a phone number yet.' in resp.text
    assert 'Your phone number is' not in resp.text
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', simple_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()

    resp.form.set('sms_code', code.value)
    resp.form.submit('').follow()
    simple_user.refresh_from_db()
    assert simple_user.attributes.phone == '+33122446666'
    assert simple_user.phone_verified_on is not None


def test_phone_change_no_existing_number_accounts_action_label_variation(
    app, simple_user, phone_activated_authn
):
    resp = login(
        app,
        simple_user,
        path='/accounts/',
    )
    assert (
        resp.pyquery("[href='/accounts/change-phone/']").text()
        == 'Declare Phone attribute used for authentication'
    )

    simple_user.attributes.phone = '+33122446666'
    simple_user.save()

    resp = app.get('/accounts/')
    assert (
        resp.pyquery("[href='/accounts/change-phone/']").text()
        == 'Change Phone attribute used for authentication'
    )


def test_change_phone_local_ou_uniqueness(app, phone_user, user_ou1, ou1, settings):
    Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    settings.A2_PHONE_IS_UNIQUE = settings.A2_REGISTRATION_PHONE_IS_UNIQUE = False

    default_ou = get_default_ou()
    default_ou.phone_is_unique = ou1.phone_is_unique = True
    default_ou.save()
    ou1.save()

    phone_user.attributes.phone = '+33122446688'
    phone_user.attributes.another_phone = '+33122444444'
    phone_user.phone = ''
    phone_user.phone_verified_on = None
    phone_user.ou = default_ou
    phone_user.save()

    user_ou1.attributes.phone = '+33122446666'  # phone already existing in another ou

    assert phone_user.phone_verified_on is None

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    assert 'Your current phone number is +33122446688.' in resp.text
    assert 'Change Phone attribute used for authentication' in resp.pyquery('title')[0].text

    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446666'
    assert phone_user.attributes.another_phone == '+33122444444'  # unchanged
    assert phone_user.phone_verified_on is not None

    SMSCode.objects.all().delete()

    phone_user.attributes.phone = '+33122446688'
    phone_user.save()
    user_ou1.ou = default_ou
    user_ou1.save()

    resp = app.get('/accounts/change-phone/')
    resp.form.set('phone_1', '122446666')
    resp.form.set('password', phone_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow().maybe_follow()
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446688'
    assert (
        resp.pyquery('ul.messages li.error').text()
        == 'This phone number is already used by another account within organizational unit Default organizational unit.'
    )


def test_phone_delete(app, phone_user, phone_activated_authn, settings):
    settings.A2_PHONE_IS_UNIQUE = settings.A2_REGISTRATION_PHONE_IS_UNIQUE = False
    settings.SMS_URL = 'https://foo.whatever.none/'

    phone_user.attributes.phone = '+33122446688'
    phone_user.save()

    phone_activated_authn.phone_identifier_field.required = False
    phone_activated_authn.phone_identifier_field.user_editable = True
    phone_activated_authn.phone_identifier_field.user_visible = True
    phone_activated_authn.phone_identifier_field.save()

    phone_activated_authn.accept_email_authentication = False
    phone_activated_authn.save()

    resp = login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/change-phone/',
    )
    assert 'Your current phone number is +33122446688.' in resp.text
    resp.form.set('phone_1', '')
    resp.form.set('password', 'user')
    resp = resp.form.submit()
    assert resp.location == '/'
    resp = resp.follow()
    assert resp.pyquery('ul.messages li.error').text() == "You can't delete your phone number."
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446688'

    phone_activated_authn.accept_email_authentication = True
    phone_activated_authn.save()
    phone_activated_authn.phone_identifier_field.required = True
    phone_activated_authn.phone_identifier_field.save()

    resp = app.get('/accounts/change-phone/')
    resp.form.set('phone_1', '')
    resp.form.set('password', 'user')
    resp = resp.form.submit()
    assert resp.location == '/'
    resp = resp.follow()
    assert resp.pyquery('ul.messages li.error').text() == "You can't delete your phone number."
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446688'

    phone_activated_authn.phone_identifier_field.required = False
    phone_activated_authn.phone_identifier_field.user_visible = False
    phone_activated_authn.phone_identifier_field.save()

    resp = app.get('/accounts/change-phone/')
    resp.form.set('phone_1', '')
    resp.form.set('password', 'user')
    resp = resp.form.submit()
    assert resp.location == '/'
    resp = resp.follow()
    assert resp.pyquery('ul.messages li.error').text() == "You can't delete your phone number."
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446688'

    phone_activated_authn.phone_identifier_field.user_visible = True
    phone_activated_authn.phone_identifier_field.user_editable = False
    phone_activated_authn.phone_identifier_field.save()

    app.get('/accounts/change-phone/', status=404)

    phone_activated_authn.phone_identifier_field.user_editable = True
    phone_activated_authn.phone_identifier_field.save()
    phone_user.username = ''
    phone_user.save()

    resp = app.get('/accounts/change-phone/')
    resp.form.set('phone_1', '')
    resp.form.set('password', 'user')
    resp = resp.form.submit()
    assert resp.location == '/'
    resp = resp.follow()
    assert not resp.pyquery('ul.messages li.error')
    assert (
        resp.pyquery('ul.messages li.warning').text()
        == 'Please declare an email address or a username before deleting '
        'your phone number, as it is currently your only identifier.'
    )
    phone_user.refresh_from_db()
    assert phone_user.attributes.phone == '+33122446688'

    phone_user.username = 'user'
    phone_user.save()

    resp = app.get('/accounts/change-phone/')
    resp.form.set('phone_1', '')
    resp.form.set('password', 'user')
    resp = resp.form.submit().follow()
    assert not resp.pyquery('ul.messages li.error')
    assert not resp.pyquery('ul.messages li.warning')
    assert resp.pyquery('ul.messages li.info').text() == 'Your phone number has been deleted.'
    phone_user.refresh_from_db()
    assert not phone_user.attributes.phone
