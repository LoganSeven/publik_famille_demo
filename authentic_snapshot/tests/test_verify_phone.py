# authentic2 - versatile identity manager
# Copyright (C) 2010-2023 Entr'ouvert
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

from authentic2.models import Attribute, SMSCode, Token

from .utils import login


def test_verify_phone(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    nomail_user.attributes.phone = '+33122446688'
    nomail_user.attributes.another_phone = '+33122444444'
    nomail_user.phone = ''
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    assert (
        'Your current unverified phone number is +33122446688. A text message will be sent in '
        'order to verify it.'
    ) in resp.text
    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    nomail_user.refresh_from_db()
    assert nomail_user.attributes.phone == '+33122446688'  # unchanged
    assert nomail_user.attributes.another_phone == '+33122444444'  # unchanged
    assert nomail_user.phone_verified_on is not None


def test_verify_phone_cancel(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone = ''
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    assert 'Your current unverified phone number is +33122446688.' in resp.text

    resp = resp.form.submit('cancel')
    assert resp.status_code == 302
    assert resp.location == '/accounts/'
    assert not SMSCode.objects.all()
    assert not Token.objects.all()

    nomail_user.refresh_from_db()
    assert nomail_user.phone_verified_on is None


def test_verify_phone_sms_input_cancel(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone = ''
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    resp = resp.form.submit('cancel')
    assert resp.status_code == 302
    assert resp.location == '/'
    assert not Token.objects.count()
    assert nomail_user.phone_verified_on is None


def test_verify_phone_erroneous_initial_data(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone = ''
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/',
        password=nomail_user.clear_password,
    )
    nomail_user.attributes.phone = 'bépovdjl'
    nomail_user.save()
    resp = app.get('/accounts/verify-phone/')
    assert not resp.form.fields['phone'][0].value


def test_verify_phone_wrong_code(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone = ''
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    assert 'Your current unverified phone number is +33122446688.' in resp.text

    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    SMSCode.objects.get()
    resp.form.set('sms_code', 'abc')
    resp = resp.form.submit('')
    assert 'Wrong SMS code.' in resp.pyquery('ul.errorlist li')[0].text
    assert not Token.objects.count()


def test_verify_phone_wrong_password(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone = ''
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    assert 'Your current unverified phone number is +33122446688.' in resp.text

    resp.form.set('password', nomail_user.clear_password + '"')
    resp = resp.form.submit()
    assert resp.pyquery('#error_id_password').text() == 'Incorrect password.'


def test_verify_phone_nondefault_attribute(app, db, nomail_user, user_ou1, phone_activated_authn, settings):
    another_phone, _ = Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        user_editable=True,
        disabled=False,
        defaults={'label': 'Another phone'},
    )
    phone_activated_authn.phone_identifier_field = another_phone
    phone_activated_authn.save()

    nomail_user.attributes.phone = '+33122446688'
    nomail_user.attributes.another_phone = '+33122444444'
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.another_phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    assert 'Your current unverified phone number is +33122444444.' in resp.text

    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    nomail_user.refresh_from_db()
    assert nomail_user.phone_verified_on is not None


def test_verify_phone_expired_code(app, nomail_user, user_ou1, phone_activated_authn, settings, freezer):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    freezer.tick(3600)  # user did not immediately submit code
    resp = resp.form.submit('')
    assert resp.pyquery('ul.errorlist li')[0].text == 'The code has expired.'
    assert not Token.objects.count()


def test_verify_phone_code_modified(app, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit()
    location = resp.location[:-5] + 'abcd/'  # oops, something went wrong with the url token
    app.get(location, status=404)
    assert not Token.objects.count()


def test_verify_phone_token_modified(app, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('')
    resp.location = resp.location.split('?')[0]
    resp.location = resp.location[:-5] + 'abcd/'  # oops, something went wrong with the url token
    resp = resp.follow().maybe_follow()
    assert resp.pyquery('.error')[0].text == 'Your phone number update request is invalid, try again'
    nomail_user.refresh_from_db()
    assert not nomail_user.phone_verified_on


def test_verify_phone_identifier_attribute_changed(
    app, nomail_user, user_ou1, phone_activated_authn, settings
):
    phone, dummy = Attribute.objects.get_or_create(
        name='another_phone',
        kind='phone_number',
        defaults={'label': 'Another phone'},
    )

    nomail_user.attributes.phone = '+33122446688'
    nomail_user.attributes.another_phone = '+33122444444'
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    assert 'Your current unverified phone number is +33122446688.' in resp.text

    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    phone_activated_authn.phone_identifier_field = phone
    phone_activated_authn.save()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    assert not Token.objects.count()
    nomail_user.refresh_from_db()
    assert nomail_user.attributes.phone == '+33122446688'  # unchanged
    assert nomail_user.attributes.another_phone == '+33122446688'  # changed
    assert nomail_user.phone_verified_on


def test_verify_phone_identifier_field_unknown(app, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/',
        password=nomail_user.clear_password,
    )

    phone_activated_authn.phone_identifier_field = None
    phone_activated_authn.save()

    app.get('/accounts/verify-phone/', status=403)


def test_verify_phone_identifier_field_not_user_editable(
    app, nomail_user, user_ou1, phone_activated_authn, settings
):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/',
        password=nomail_user.clear_password,
    )

    phone_activated_authn.phone_identifier_field.user_editable = False
    phone_activated_authn.phone_identifier_field.save()

    app.get('/accounts/verify-phone/', status=404)


def test_verify_phone_identifier_field_disabled(app, nomail_user, user_ou1, phone_activated_authn, settings):
    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/',
        password=nomail_user.clear_password,
    )

    phone_activated_authn.phone_identifier_field.disabled = True
    phone_activated_authn.phone_identifier_field.save()

    app.get('/accounts/verify-phone/', status=403)


def test_verify_change_lock_identifier_error_token_use(
    app, nomail_user, user_ou1, phone_activated_authn, settings, monkeypatch
):
    from authentic2.models import Lock

    nomail_user.attributes.phone = '+33122446688'
    nomail_user.phone_verified_on = None
    nomail_user.save()

    settings.SMS_URL = 'https://foo.whatever.none/'

    def erroneous_lock_identifier(identifier, nowait=False):
        raise Lock.Error

    resp = login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/verify-phone/',
        password=nomail_user.clear_password,
    )
    resp.form.set('password', nomail_user.clear_password)
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()

    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('')
    monkeypatch.setattr(Lock, 'lock_identifier', erroneous_lock_identifier)
    resp = resp.follow().maybe_follow()
    assert 'Something went wrong while updating' in resp.pyquery('li.error')[0].text
    nomail_user.refresh_from_db()
    assert not nomail_user.phone_verified_on
