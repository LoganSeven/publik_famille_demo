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

import io
import json
import unittest.mock
import urllib.parse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.cache import SessionStore
from django.core.serializers.json import DjangoJSONEncoder
from django.test import TestCase
from django.test.client import Client
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.translation import gettext as _

from authentic2 import attribute_kinds, models
from authentic2.utils.misc import continue_to_next_url, login_require, make_url, redirect, redirect_to_login

from .utils import Authentic2TestCase, assert_event, call_command, get_response_form


class SerializerTests(TestCase):
    def test_generic_foreign_key_natural_key(self):
        from django.core import serializers

        from authentic2.models import Attribute, AttributeValue

        User = get_user_model()
        ucount = User.objects.count()
        acount = Attribute.objects.count()
        u = User.objects.create(username='john.doe')
        avcount = AttributeValue.objects.count()
        a = Attribute.objects.create(name='phone', label='phone', kind='string')
        av = AttributeValue.objects.create(owner=u, attribute=a, content='0101010101')
        self.assertEqual(User.objects.count(), ucount + 1)
        self.assertEqual(Attribute.objects.count(), acount + 1)
        self.assertEqual(AttributeValue.objects.count(), avcount + 1)
        s = serializers.get_serializer('json')()
        s.serialize([u, a, av], use_natural_foreign_keys=True, use_natural_primary_keys=True)
        result = s.getvalue()
        u.delete()
        a.delete()
        self.assertEqual(User.objects.count(), ucount)
        self.assertEqual(Attribute.objects.count(), acount)
        self.assertEqual(AttributeValue.objects.count(), 0)
        expected = [
            {
                'model': 'custom_user.user',
                'fields': {
                    'uuid': u.uuid,
                    'email_verified': False,
                    'email_verified_date': None,
                    'email_verified_sources': '[]',  # weird ArrayField serialization behavior
                    'username': 'john.doe',
                    'email': '',
                    'phone': None,
                    'phone_verified_on': None,
                    'first_name': '',
                    'last_name': '',
                    'is_active': True,
                    'is_staff': False,
                    'is_superuser': False,
                    'last_login': u.last_login,
                    'last_account_deletion_alert': None,
                    'date_joined': u.date_joined,
                    'modified': u.modified,
                    'groups': [],
                    'user_permissions': [],
                    'password': '',
                    'ou': None,
                    'deactivation': None,
                    'deactivation_reason': None,
                    'keepalive': None,
                },
            },
            {
                'model': 'authentic2.attribute',
                'fields': {
                    'description': '',
                    'name': 'phone',
                    'label': 'phone',
                    'kind': 'string',
                    'user_editable': False,
                    'asked_on_registration': False,
                    'multiple': False,
                    'user_visible': False,
                    'required': False,
                    'disabled': False,
                    'searchable': False,
                    'order': 0,
                    'scopes': '',
                    'required_on_login': False,
                },
            },
            {
                'model': 'authentic2.attributevalue',
                'fields': {
                    'owner': [['custom_user', 'user'], [u.uuid]],
                    'attribute': ['phone'],
                    'content': '0101010101',
                    'multiple': False,
                    'verified': False,
                    'last_verified_on': None,
                    'search_vector': None,
                },
            },
        ]
        expected = json.loads(json.dumps(expected, cls=DjangoJSONEncoder))
        for obj in serializers.deserialize('json', result):
            obj.save()
        assert json.loads(result) == expected
        self.assertEqual(User.objects.count(), ucount + 1)
        self.assertEqual(Attribute.objects.count(), acount + 1)
        # first_name and last_name attribute value not recreated since they were not dumped
        self.assertEqual(AttributeValue.objects.count(), 1)


class UtilsTests(Authentic2TestCase):
    def test_assert_equals_url(self):
        self.assertEqualsURL('/test?coin=1&bob=2&coin=3', '/test?bob=2&coin=1&coin=3')

    def test_make_url(self):
        self.assertEqualsURL(make_url('../coin'), '../coin')
        self.assertEqualsURL(make_url('../boob', params={'next': '..'}), '../boob?next=..')
        self.assertEqualsURL(
            make_url('../boob', params={'next': '..'}, append={'xx': 'yy'}), '../boob?xx=yy&next=..'
        )
        self.assertEqualsURL(
            make_url('../boob', params={'next': '..'}, append={'next': 'yy'}), '../boob?next=..&next=yy'
        )
        self.assertEqualsURL(make_url('auth_login', params={'next': '/zob'}), '/login/?next=%2Fzob')
        self.assertEqualsURL(
            make_url('auth_login', params={'next': '/zob'}, fragment='a2-panel'),
            '/login/?next=%2Fzob#a2-panel',
        )

    def test_redirect(self):
        from django.test.client import RequestFactory

        rf = RequestFactory()
        request = rf.get('/coin', data={'next': '..'})
        request2 = rf.get('/coin', data={'next': '..', 'token': 'xxx'})
        response = redirect(request, '/boob/', keep_params=True)
        self.assertEqualsURL(response['Location'], '/boob/?next=..')
        response = redirect(request, '/boob/', keep_params=True, exclude=['next'])
        self.assertEqualsURL(response['Location'], '/boob/')
        response = redirect(request2, '/boob/', keep_params=True)
        self.assertEqualsURL(response['Location'], '/boob/?token=xxx&next=..')
        response = redirect(request, '/boob/', keep_params=True, exclude=['token'])
        self.assertEqualsURL(response['Location'], '/boob/?next=..')
        response = redirect(request, '/boob/', keep_params=True, include=['next'])
        self.assertEqualsURL(response['Location'], '/boob/?next=..')
        response = redirect(request, '/boob/', keep_params=True, include=['next'], params={'token': 'uuu'})
        self.assertEqualsURL(response['Location'], '/boob/?token=uuu&next=..')

    def test_redirect_to_login(self):
        from django.test.client import RequestFactory

        rf = RequestFactory()
        request = rf.get('/coin', data={'next': '..'})
        response = redirect_to_login(request)
        self.assertEqualsURL(response['Location'], '/login/?next=..')

    def test_continue_to_next_url(self):
        from django.test.client import RequestFactory

        rf = RequestFactory()
        request = rf.get('/coin', data={'next': '/zob/', 'nonce': 'xxx'})
        response = continue_to_next_url(request)
        self.assertEqualsURL(response['Location'], '/zob/?nonce=xxx')

    def test_login_require(self):
        from django.test.client import RequestFactory

        rf = RequestFactory()
        request = rf.get('/coin', data={'next': '/zob/', 'nonce': 'xxx'})
        request.session = SessionStore()
        response = login_require(request, login_hint=['backoffice'])
        self.assertEqualsURL(response['Location'].split('?', 1)[0], '/login/')
        self.assertEqualsURL(
            urllib.parse.parse_qs(response['Location'].split('?', 1)[1])['next'][0],
            '/coin?nonce=xxx&next=/zob/',
        )
        self.assertEqual(request.session['login-hint'], ['backoffice'])


class UserProfileTests(TestCase):
    def setUp(self):
        User = get_user_model()
        user = User.objects.create(username='testbot')
        user.set_password('secret')
        user.save()
        self.user = user
        self.client = Client()

    def test_edit_profile_attributes(self):
        # disable existing attributes
        models.Attribute.objects.update(disabled=True)

        models.Attribute.objects.create(
            label='custom',
            name='custom',
            required=True,
            user_visible=True,
            user_editable=True,
            kind='string',
        )
        models.Attribute.objects.create(
            label='ID', name='national_number', user_editable=True, user_visible=True, kind='string'
        )
        self.assertTrue(self.client.login(request=None, username='testbot', password='secret'))

        # get the edit page in order to check form's prefix
        response = self.client.get(reverse('profile_edit'))
        form = get_response_form(response)

        kwargs = {'custom': 'random data', 'national_number': 'xx20153566342yy'}
        if form.prefix:
            kwargs = {'%s-%s' % (form.prefix, k): v for k, v in kwargs.items()}

        response = self.client.post(reverse('profile_edit'), kwargs)
        new = {'custom': 'random data', 'national_number': 'xx20153566342yy'}
        assert_event('user.profile.edit', user=self.user, session=self.client.session, old={}, new=new)

        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('account_management'))
        self.assertContains(response, 'random data')
        self.assertContains(response, 'xx20153566342yy')

        response = self.client.get(reverse('profile_edit'))
        form = get_response_form(response)
        self.assertEqual(form['custom'].value(), 'random data')
        self.assertEqual(form['national_number'].value(), 'xx20153566342yy')

    def test_noneditable_profile_attributes(self):
        """
        tests if user non editable attributes do not appear in profile form
        """
        # disable existing attributes
        models.Attribute.objects.update(disabled=True)

        models.Attribute.objects.create(
            label='custom', name='custom', required=False, user_editable=False, kind='string'
        )
        models.Attribute.objects.create(
            label='ID', name='national_number', user_editable=False, user_visible=False, kind='string'
        )

        self.assertTrue(self.client.login(request=None, username='testbot', password='secret'))
        response = self.client.get(reverse('profile_edit'))
        form = get_response_form(response)
        self.assertEqual(set(form.fields), {'next_url'})


class CacheTests(TestCase):
    @pytest.fixture(autouse=True)
    def cache_settings(self, settings):
        settings.A2_CACHE_ENABLED = True

    @override_settings(ROOT_URLCONF='tests.cache_urls')
    def test_cache_decorator_base(self):
        import random

        from authentic2.utils.cache import CacheDecoratorBase

        class GlobalCache(CacheDecoratorBase):
            def __init__(self, *args, **kwargs):
                self.cache = {}
                super().__init__(*args, **kwargs)

            def set(self, key, value):
                self.cache[key] = value

            def get(self, key):
                return self.cache.get(key, (None, None))

            def delete(self, key, value):
                if key in self.cache and self.cache[key] == value:
                    del self.cache[key]

        def f():
            return random.random()

        def f2(a, b):
            return a

        # few chances the same value comme two times in a row
        self.assertNotEqual(f(), f())

        # with cache the same value will come back
        g = GlobalCache(f, hostname_vary=False)
        values = set()
        for _ in range(10):
            values.add(g())
        self.assertEqual(len(values), 1)
        # with and hostname vary 10 values will come back
        g = GlobalCache(f, hostname_vary=True)
        values = set()
        for _ in range(10):
            values.add(g())
        self.assertEqual(len(values), 10)
        # null timeout, no cache
        h = GlobalCache(timeout=0)(f)
        self.assertNotEqual(h(), h())
        # vary on second arg
        i = GlobalCache(hostname_vary=False, args=(1,))(f2)
        for a in range(1, 10):
            self.assertEqual(i(a, 1), 1)
        for a in range(2, 10):
            self.assertEqual(i(a, a), a)

    @override_settings(ROOT_URLCONF='tests.cache_urls')
    def test_django_cache(self):
        response1 = self.client.get('/django_cache/', headers={'host': 'cache1.example.com'})
        response2 = self.client.get('/django_cache/', headers={'host': 'cache2.example.com'})
        response3 = self.client.get('/django_cache/', headers={'host': 'cache1.example.com'})
        self.assertNotEqual(response1.content, response2.content)
        self.assertEqual(response1.content, response3.content)

    @override_settings(ROOT_URLCONF='tests.cache_urls')
    def test_session_cache(self):
        client = Client()
        response1 = client.get('/session_cache/')
        response2 = client.get('/session_cache/')
        client = Client()
        response3 = client.get('/session_cache/')
        self.assertEqual(response1.content, response2.content)
        self.assertNotEqual(response1.content, response3.content)


class AttributeKindsTest(TestCase):
    def test_simple(self):
        from django import forms
        from django.core.exceptions import ValidationError

        with self.settings(
            A2_ATTRIBUTE_KINDS=[
                {
                    'label': 'integer',
                    'name': 'integer',
                    'field_class': forms.IntegerField,
                }
            ]
        ):
            title_field = attribute_kinds.get_form_field('title')
            self.assertTrue(isinstance(title_field, forms.ChoiceField))
            self.assertTrue(isinstance(title_field.widget, forms.RadioSelect))
            self.assertIsNotNone(title_field.choices)
            self.assertTrue(isinstance(attribute_kinds.get_form_field('string'), forms.CharField))
            self.assertEqual(attribute_kinds.get_kind('string')['name'], 'string')
            self.assertTrue(isinstance(attribute_kinds.get_form_field('integer'), forms.IntegerField))
            self.assertEqual(attribute_kinds.get_kind('integer')['name'], 'integer')
            attribute_kinds.validate_siret('49108189900024')
            with self.assertRaises(ValidationError):
                attribute_kinds.validate_siret('49108189900044')
        with self.assertRaises(KeyError):
            attribute_kinds.get_form_field('integer')
        with self.assertRaises(KeyError):
            attribute_kinds.get_kind('integer')
        fields = {}
        for i, name in enumerate(attribute_kinds.get_attribute_kinds()):
            fields['field_%d' % i] = attribute_kinds.get_form_field(name)
        AttributeKindForm = type('AttributeKindForm', (forms.Form,), fields)
        str(AttributeKindForm().as_p())


def test_slug_from_name_default():
    from authentic2.api_views import SlugFromNameDefault

    default = SlugFromNameDefault()
    assert (
        default(unittest.mock.Mock(context={'request': unittest.mock.Mock(data={'name': '__N.ame___'})}))
        == '__name___'
    )


@pytest.mark.parametrize('debug', (True, False))
def test_manage_check(debug):
    with override_settings(DEBUG=debug):
        # Reimport with overriden settings
        import authentic2.idp.saml.urls  # pylint: disable=unused-import
        import authentic2.idp.urls  # pylint: disable=unused-import
        import authentic2.manager.urls  # pylint: disable=unused-import
        import authentic2.urls  # pylint: disable=unused-import
        import authentic2_auth_fc.urls  # pylint: disable=unused-import
        import authentic2_auth_oidc.urls  # pylint: disable=unused-import
        import authentic2_auth_saml.urls  # pylint: disable=unused-import
        import authentic2_idp_cas.urls  # pylint: disable=unused-import
        import authentic2_idp_oidc.manager.urls  # pylint: disable=unused-import
        import authentic2_idp_oidc.urls  # pylint: disable=unused-import

        output = io.StringIO()
        call_command('check', stdout=output, stderr=output)
        output.seek(0)
        result = output.read()
        assert result.startswith('System check identified no issue')

    # Reimport without settings overriden
    import authentic2.idp.saml.urls  # pylint: disable=unused-import
    import authentic2.idp.urls  # pylint: disable=unused-import
    import authentic2.manager.urls  # pylint: disable=unused-import
    import authentic2.urls  # pylint: disable=unused-import
    import authentic2_auth_fc.urls  # pylint: disable=unused-import
    import authentic2_auth_oidc.urls  # pylint: disable=unused-import
    import authentic2_auth_saml.urls  # pylint: disable=unused-import
    import authentic2_idp_cas.urls  # pylint: disable=unused-import
    import authentic2_idp_oidc.manager.urls  # pylint: disable=unused-import
    import authentic2_idp_oidc.urls  # pylint: disable=unused-import
