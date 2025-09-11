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

import base64
import datetime
import os

import PIL.Image
import pytest
from django.conf import settings
from webtest import Upload

from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.custom_user.models import User
from authentic2.models import Attribute

from .utils import get_link_from_mail


def test_string(db, app, admin, mailoutbox):
    Attribute.objects.create(
        name='nom_de_naissance', label='Nom de naissance', kind='string', asked_on_registration=True
    )
    qs = User.objects.filter(first_name='John')

    response = app.get('/register/')
    form = response.form
    form.set('email', 'john.doe@example.com')
    response = form.submit().follow()
    assert 'john.doe@example.com' in response
    url = get_link_from_mail(mailoutbox[0])
    response = app.get(url)

    form = response.form
    assert response.pyquery('#id_nom_de_naissance').attr('maxlength') == '256'
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('nom_de_naissance', '1234567890' * 30)
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_nom_de_naissance')

    form = response.form
    form.set('nom_de_naissance', 'Noël')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.nom_de_naissance == 'Noël'
    qs.delete()

    app.authorization = ('Basic', (admin.username, admin.clear_password))
    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'nom_de_naissance': '1234567890' * 30,
    }
    app.post_json('/api/users/', params=payload, status=400)

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'nom_de_naissance': 'Noël',
    }
    app.post_json('/api/users/', params=payload, status=201)
    assert qs.get().attributes.nom_de_naissance == 'Noël'
    qs.delete()


def test_fr_postcode(db, app, admin, mailoutbox):
    def register_john():
        response = app.get('/register/')
        form = response.form
        form.set('email', 'john.doe@example.com')
        response = form.submit().follow()
        assert 'john.doe@example.com' in response
        return get_link_from_mail(mailoutbox[-1])

    Attribute.objects.create(
        name='postcode', label='postcode', kind='fr_postcode', asked_on_registration=True
    )
    qs = User.objects.filter(first_name='John')

    url = register_john()
    response = app.get(url)

    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('postcode', 'abc')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_postcode')

    form = response.form
    form.set('postcode', '123')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_postcode')

    form = response.form
    form.set('postcode', '12345')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.postcode == '12345'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('postcode', ' 12345 ')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.postcode == '12345'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('postcode', '')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.postcode == ''
    qs.delete()

    app.authorization = ('Basic', (admin.username, admin.clear_password))

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'postcode': ' 1234abc ',
    }
    app.post_json('/api/users/', params=payload, status=400)

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'postcode': '1234',
    }
    app.post_json('/api/users/', params=payload, status=400)

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'postcode': '12345',
    }
    app.post_json('/api/users/', params=payload)
    assert qs.get().attributes.postcode == '12345'
    qs.delete()

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'postcode': None,
    }
    app.post_json('/api/users/', params=payload)
    assert qs.get().attributes.postcode is None
    qs.delete()

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'postcode': '',
    }
    app.post_json('/api/users/', params=payload)
    assert qs.get().attributes.postcode == ''
    qs.delete()


def test_phone_number(db, app, admin, mailoutbox, settings):
    LoginPasswordAuthenticator.objects.update(emails_address_ratelimit='')

    def register_john():
        response = app.get('/register/')
        form = response.form
        form.set('email', 'john.doe@example.com')
        response = form.submit().follow()
        assert 'john.doe@example.com' in response
        return get_link_from_mail(mailoutbox[-1])

    Attribute.objects.create(
        name='phone_number', label='Second Phone', kind='phone_number', asked_on_registration=True
    )
    qs = User.objects.filter(first_name='John')

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number_0', '33')
    form.set('phone_number_1', 'abc')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number_0')

    form = response.form
    assert not response.pyquery('#id_phone_number_1').attr('maxlength')
    form.set('phone_number_0', '33')
    form.set('phone_number_1', '1234512345' * 10)
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number_0')

    form = response.form
    form.set('phone_number_0', '33')
    form.set('phone_number_1', ' +  1.2-3 4 5 ')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number_0')

    form = response.form
    form.set('phone_number_0', '33')
    form.set('phone_number_1', '123456789')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '+33123456789'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number_0', '32')
    form.set('phone_number_1', '081000000')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '+3281000000'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number_0', '33')
    form.set('phone_number_1', '')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == ''
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number_0', '33')
    form.set('phone_number_1', '1 234 5678 9')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '+33123456789'
    qs.delete()


def test_french_phone_number(db, app, admin, mailoutbox, settings):
    LoginPasswordAuthenticator.objects.update(emails_address_ratelimit='')

    def register_john():
        response = app.get('/register/')
        form = response.form
        form.set('email', 'john.doe@example.com')
        response = form.submit().follow()
        assert 'john.doe@example.com' in response
        return get_link_from_mail(mailoutbox[-1])

    Attribute.objects.create(
        name='phone_number', label='phone', kind='fr_phone_number', asked_on_registration=True
    )
    qs = User.objects.filter(first_name='John')

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', 'abc')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number')

    form = response.form
    form.set('phone_number', '1234512345' * 10)
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number')

    form = response.form
    form.set('phone_number', '12345')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number')

    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', '+12345')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number')

    form = response.form
    form.set('phone_number', ' +  1.2-3  4 5 ')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number')

    form = response.form
    form.set('phone_number', '1234567890')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_phone_number')

    form = response.form
    form.set('phone_number', '')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == ''
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', '0123456789')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '0123456789'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', '01 23 45 67 89')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '0123456789'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', '01-23-45-67-89')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '0123456789'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', '06.99.99.99.99')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '0699999999'
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('phone_number', '0699999999')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit().follow()
    assert qs.get().attributes.phone_number == '0699999999'
    qs.delete()


def test_birthdate(db, app, admin, mailoutbox, freezer):
    def register_john():
        response = app.get('/register/')
        form = response.form
        form.set('email', 'john.doe@example.com')
        response = form.submit().follow()
        assert 'john.doe@example.com' in response
        return get_link_from_mail(mailoutbox[-1])

    freezer.move_to('2018-01-01')
    Attribute.objects.create(
        name='birthdate', label='birthdate', kind='birthdate', asked_on_registration=True
    )
    qs = User.objects.filter(first_name='John')

    url = register_john()
    response = app.get(url)

    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('birthdate', '2018-01-01')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_birthdate')

    form.set('birthdate', '2017-12-31')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    form.submit().follow()
    assert qs.get().attributes.birthdate == datetime.date(2017, 12, 31)
    qs.delete()

    url = register_john()
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('birthdate', '1899-12-31')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_birthdate')

    form.set('birthdate', '1900-01-01')
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    form.submit().follow()
    assert qs.get().attributes.birthdate == datetime.date(1900, 1, 1)
    qs.delete()


def test_birthdate_api(db, app, admin, mailoutbox, freezer):
    freezer.move_to('2018-01-01')
    Attribute.objects.create(
        name='birthdate', label='birthdate', kind='birthdate', asked_on_registration=True
    )
    qs = User.objects.filter(first_name='John')
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'birthdate': '2018-01-01',
    }
    app.post_json('/api/users/', params=payload, status=400)

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'birthdate': '2017-12-31',
    }
    app.post_json('/api/users/', params=payload)
    assert qs.get().attributes.birthdate == datetime.date(2017, 12, 31)
    qs.delete()

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'birthdate': '1899-12-31',
    }
    app.post_json('/api/users/', params=payload, status=400)

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'birthdate': '1900-01-01',
    }
    app.post_json('/api/users/', params=payload)
    assert qs.get().attributes.birthdate == datetime.date(1900, 1, 1)
    qs.delete()


def test_birthdate_buggy_type(db, admin):
    attr = Attribute.objects.create(
        name='birthdate', label='birthdate', kind='birthdate', asked_on_registration=True
    )
    attr.set_value(owner=admin, value='2000-01-01')
    admin.refresh_from_db()
    assert admin.attributes.birthdate is None


def test_date_buggy_type(db, admin):
    attr = Attribute.objects.create(name='date', label='date', kind='date', asked_on_registration=True)
    attr.set_value(owner=admin, value='2000-01-01')
    admin.refresh_from_db()
    assert admin.attributes.date is None


def test_profile_image(db, app, admin, mailoutbox):
    Attribute.objects.create(
        name='cityscape_image',
        label='cityscape',
        kind='profile_image',
        asked_on_registration=True,
        required=False,
        user_visible=True,
        user_editable=True,
    )

    def john():
        return User.objects.get(first_name='John')

    response = app.get('/register/')
    form = response.form
    form.set('email', 'john.doe@example.com')
    response = form.submit().follow()
    assert 'john.doe@example.com' in response
    url = get_link_from_mail(mailoutbox[0])
    response = app.get(url)

    # verify empty file is refused
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('cityscape_image', Upload('/dev/null'))
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_cityscape_image')

    # verify 200x200 image is accepted
    form = response.form
    form.set('cityscape_image', Upload('tests/200x200.jpg'))
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert john().attributes.cityscape_image
    profile_filename = john().attributes.cityscape_image.name
    assert profile_filename.endswith('.jpeg')

    # verify API serves absolute URL for profile images
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    response = app.get('/api/users/%s/' % john().uuid)
    assert (
        response.json['cityscape_image']
        == 'https://testserver/media/%s' % john().attributes.cityscape_image.name
    )
    app.authorization = None

    # verify we can clear the image
    response = app.get('/accounts/edit/')
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('cityscape_image-clear', True)
    response = form.submit()
    assert john().attributes.cityscape_image is None

    # verify API serves None for empty profile images
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    response = app.get('/api/users/%s/' % john().uuid)
    assert response.json['cityscape_image'] is None

    # verify 201x201 image is accepted and resized
    response = app.get('/accounts/edit/')
    form = response.form
    form.set('cityscape_image', Upload('tests/201x201.jpg'))
    response = form.submit()
    with PIL.Image.open(os.path.join(settings.MEDIA_ROOT, john().attributes.cityscape_image.name)) as image:
        assert image.width == 200
        assert image.height == 200
    assert john().attributes.cityscape_image.name != profile_filename

    # verify file input mentions image files
    response = app.get('/accounts/edit/')
    form = response.form
    assert form['cityscape_image'].attrs['accept'] == 'image/*'

    # clear image via API, by putting empty JSON
    response = app.put_json('/api/users/%s/' % john().uuid, params={'cityscape_image': ''})
    assert john().attributes.cityscape_image is None

    # put back first image via API, by putting base64 encoded JSON
    with open('tests/200x200.jpg', 'rb') as f:
        image = f.read()
    base64_image = base64.b64encode(image).decode()
    response = app.put_json('/api/users/%s/' % john().uuid, params={'cityscape_image': base64_image})
    assert john().attributes.cityscape_image
    profile_filename = john().attributes.cityscape_image.name
    assert profile_filename.endswith('.jpeg')

    # verify 201x201 image is accepted and resized by API
    with open('tests/201x201.jpg', 'rb') as f:
        image = f.read()
    base64_image = base64.b64encode(image).decode()
    response = app.put_json('/api/users/%s/' % john().uuid, params={'cityscape_image': base64_image})
    with PIL.Image.open(os.path.join(settings.MEDIA_ROOT, john().attributes.cityscape_image.name)) as image:
        assert image.width == 200
        assert image.height == 200
    assert john().attributes.cityscape_image.name != profile_filename

    # put back first image via API, by putting data URI representing a base64 encoded image using JSON
    data_uri = 'data:%s;base64,%s' % ('image/jpeg', base64_image)
    response = app.put_json('/api/users/%s/' % john().uuid, params={'cityscape_image': data_uri})
    assert john().attributes.cityscape_image
    profile_filename = john().attributes.cityscape_image.name
    assert profile_filename.endswith('.jpeg')

    # bad request on invalid b64
    response = app.put_json(
        '/api/users/%s/' % john().uuid, params={'cityscape_image': 'invalid_64'}, status=400
    )

    # clear image via API, not using JSON
    response = app.put('/api/users/%s/' % john().uuid, params={'cityscape_image': ''})
    assert john().attributes.cityscape_image is None

    # put back first image via API, not using JSON
    response = app.put(
        '/api/users/%s/' % john().uuid, params={'cityscape_image': Upload('tests/200x200.jpg')}
    )
    assert john().attributes.cityscape_image
    profile_filename = john().attributes.cityscape_image.name
    assert profile_filename.endswith('.jpeg')


# Images generated with :
# im = Image.new('RGB', (100,100))
# im.save('metadata_too_large.png', icc_profile=b'a'*(1024*1024*2))
# meta = PIL.PngPlugin.PngInfo()
# meta.add_text('author', 'a'*1024*1024*64, zip=True)
# im.save('metadata_too_author.png', pnginfo=meta)
@pytest.mark.parametrize(
    'bad_image_path',
    (
        'tests/metadata_too_large_author.png',
        'tests/metadata_too_large_icc.png',
    ),
)
def test_profile_image_metadata_too_large(db, app, admin, mailoutbox, bad_image_path):
    Attribute.objects.create(
        name='cityscape_image',
        label='cityscape',
        kind='profile_image',
        asked_on_registration=True,
        required=False,
        user_visible=True,
        user_editable=True,
    )

    response = app.get('/register/')
    form = response.form
    form.set('email', 'john.doe@example.com')
    response = form.submit().follow()
    assert 'john.doe@example.com' in response
    url = get_link_from_mail(mailoutbox[0])
    response = app.get(url)

    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('cityscape_image', Upload(bad_image_path))
    form.set('password1', '12345abcdA')
    form.set('password2', '12345abcdA')
    response = form.submit()
    assert response.pyquery.find('.form-field-error #id_cityscape_image')


def test_multiple_attribute_setter(db, app, simple_user):
    nicks = Attribute.objects.create(
        name='nicknames',
        label='Nicknames',
        kind='string',
        required=False,
        multiple=True,
        user_visible=True,
        user_editable=True,
    )

    simple_user.attributes.nicknames = ['Roger', 'Tony', 'Robie']
    simple_user.save()
    assert 'Tony' in [atv.content for atv in simple_user.attribute_values.filter(attribute=nicks)]

    simple_user.attributes.nicknames = ['Roger', 'Timmy', 'Robie']
    simple_user.save()
    assert not 'Tony' in [atv.content for atv in simple_user.attribute_values.filter(attribute=nicks)]
