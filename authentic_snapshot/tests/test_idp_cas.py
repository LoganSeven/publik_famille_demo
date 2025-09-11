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

import urllib.parse

from django.contrib.auth import get_user_model
from django.test.client import Client, RequestFactory
from django.test.utils import override_settings
from django.utils.encoding import force_str

from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.constants import AUTHENTICATION_EVENTS_SESSION_KEY, NONCE_FIELD_NAME
from authentic2_idp_cas import constants
from authentic2_idp_cas.models import Attribute, Service, Ticket

from .utils import Authentic2TestCase, assert_event

CAS_NAMESPACES = {
    'cas': constants.CAS_NAMESPACE,
}

User = get_user_model()


@override_settings(A2_IDP_CAS_ENABLE=True)
class CasTests(Authentic2TestCase):
    LOGIN = 'test'
    PASSWORD = 'test'
    EMAIL = 'test@example.com'
    FIRST_NAME = 'John'
    LAST_NAME = 'Doe'
    NAME = 'CAS service'
    SLUG = 'cas-service'
    URL = 'https://casclient.com/%C3%A9/'
    NAME2 = 'CAS service2'
    SLUG2 = 'cas-service2'
    URL2 = 'https://casclient2.com/ https://other.com/'
    SERVICE2_URL = 'https://casclient2.com/service/'
    PGT_URL = 'https://casclient.con/pgt/'

    def setUp(self):
        self.user = User.objects.create_user(
            self.LOGIN,
            password=self.PASSWORD,
            email=self.EMAIL,
            first_name=self.FIRST_NAME,
            last_name=self.LAST_NAME,
        )
        self.service = Service.objects.create(
            name=self.NAME,
            slug=self.SLUG,
            urls=self.URL,
            identifier_attribute='django_user_username',
            ou=get_default_ou(),
            logout_url=self.URL + 'logout/',
        )
        self.service_attribute1 = Attribute.objects.create(
            service=self.service, slug='email', attribute_name='django_user_email'
        )
        self.service2 = Service.objects.create(
            name=self.NAME2,
            slug=self.SLUG2,
            urls=self.URL2,
            ou=get_default_ou(),
            identifier_attribute='django_user_email',
        )
        self.service2_attribute1 = Attribute.objects.create(
            service=self.service2, slug='username', attribute_name='django_user_username'
        )
        self.authorized_service = Role.objects.create(name='rogue', ou=get_default_ou())
        self.factory = RequestFactory()

    def test_long_urls(self):
        self.service.urls = (
            'https://casclient.com/%C3%A9/lorem/ipsum/dolor/sit/amet/consectetur/adipiscing/elit/sed/do/eiusm'
            'od/tempor/incididunt/ut/labore/et/dolore/magna/aliqua/ut/enim/ad/minim/veniam/quis/nostrud/exerc'
            'itation/ullamco/laboris/nisi/ut/aliquip/ex/ea/commodo/consequat/duis/aute/irure/dolor/in/reprehe'
            'nderit/in/voluptate/velit/esse/cillum/dolore/eu/fugiat/nulla/pariatur/excepteur/sint/occaecat/cu'
            'pidatat/non/proident/sunt/in/culpa/qui/officia/deserunt/mollit/anim/id/est/laborum'
        )
        self.service.save()

    def test_service_matching(self):
        self.service.clean()
        self.service2.clean()
        self.assertEqual(Service.objects.for_service(self.URL), self.service)
        for service in self.URL2.split():
            self.assertEqual(Service.objects.for_service(service), self.service2)
        self.assertEqual(Service.objects.for_service('http://google.com'), None)

    def test_login_failure(self):
        client = Client()
        response = client.get('/idp/cas/login')
        self.assertEqual(response.status_code, 400)
        self.assertIn('no service', force_str(response.content))
        response = client.get('/idp/cas/login', {constants.SERVICE_PARAM: 'http://google.com/'})
        self.assertRedirectsComplex(response, 'http://google.com/')
        response = client.get(
            '/idp/cas/login',
            {constants.SERVICE_PARAM: self.URL, constants.RENEW_PARAM: '', constants.GATEWAY_PARAM: ''},
        )
        self.assertRedirectsComplex(response, self.URL)
        response = client.get(
            '/idp/cas/login', {constants.SERVICE_PARAM: self.URL, constants.GATEWAY_PARAM: ''}
        )
        self.assertRedirectsComplex(response, self.URL)

    def test_role_access_control_denied_on_continue(self):
        client = Client()
        service = self.service
        service.add_authorized_role(self.authorized_service)
        service.unauthorized_url = 'https://casclient.com/loser/'
        service.save()
        assert service.authorized_roles.exists() is True
        response = client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        location = response['Location']
        query = urllib.parse.parse_qs(location.split('?')[1])
        dummy_next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        response = client.get(location)
        response = client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        response = client.get(response.url)
        assert_event(
            'user.service.sso.denial',
            session=client.session,
            user=self.user,
            service=self.service,
        )
        self.assertIn('https://casclient.com/loser/', force_str(response.content))

    def test_role_access_control_granted_on_continue(self):
        client = Client()
        service = self.service
        service.add_authorized_role(self.authorized_service)
        User.objects.get(username=self.LOGIN).roles.add(self.authorized_service)
        assert service.authorized_roles.exists() is True
        response = client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        location = response['Location']
        query = urllib.parse.parse_qs(location.split('?')[1])
        dummy_next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        response = client.get(location)
        response = client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        response = client.get(response.url)
        client = Client()
        ticket_id = urllib.parse.parse_qs(response.url.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/validate', {constants.TICKET_PARAM: ticket_id, constants.SERVICE_PARAM: self.URL}
        )

    def test_role_access_control_granted_on_login(self):
        client = Client()
        # Firstly, connect
        client.get('/login/')
        client.post(
            '/login/',
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        service = self.service
        service.add_authorized_role(self.authorized_service)
        User.objects.get(username=self.LOGIN).roles.add(self.authorized_service)
        assert service.authorized_roles.exists() is True
        response = client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        location = response['Location']
        client = Client()
        ticket_id = urllib.parse.parse_qs(location.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/validate', {constants.TICKET_PARAM: ticket_id, constants.SERVICE_PARAM: self.URL}
        )

    def test_role_access_control_denied_on_login(self):
        client = Client()
        # Firstly, connect
        client.get('/login/')
        client.post(
            '/login/',
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        service = self.service
        service.add_authorized_role(self.authorized_service)
        service.unauthorized_url = 'https://casclient.com/loser/'
        service.save()
        assert service.authorized_roles.exists() is True
        response = client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        assert_event(
            'user.service.sso.denial',
            session=client.session,
            user=self.user,
            service=self.service,
        )
        self.assertIn('https://casclient.com/loser/', force_str(response.content))

    def test_login_validate(self):
        response = self.client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get()
        location = response['Location']
        url = location.split('?')[0]
        query = urllib.parse.parse_qs(location.split('?')[1])
        self.assertTrue(url.endswith('/login/'))
        self.assertIn('nonce', query)
        self.assertIn('next', query)
        self.assertEqual(query['nonce'], [ticket.ticket_id])
        next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        self.assertEqual(next_url, '/idp/cas/continue/')
        self.assertEqual(set(next_url_query.keys()), {constants.SERVICE_PARAM, NONCE_FIELD_NAME})
        self.assertEqual(next_url_query[constants.SERVICE_PARAM], [self.URL])
        self.assertEqual(next_url_query[NONCE_FIELD_NAME], [ticket.ticket_id])
        response = self.client.get(location)
        response = self.client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        self.assertIn(AUTHENTICATION_EVENTS_SESSION_KEY, self.client.session)
        self.assertIn('nonce', self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0])
        self.assertIn(ticket.ticket_id, self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0]['nonce'])
        self.assertRedirectsComplex(response, query['next'][0], nonce=ticket.ticket_id)
        response = self.client.get(response.url)
        self.assertRedirectsComplex(response, self.URL, ticket=ticket.ticket_id)
        # Check logout state has been updated
        ticket = Ticket.objects.get()
        self.assertIn(constants.SESSION_CAS_LOGOUTS, self.client.session)
        self.assertEqual(
            self.client.session[constants.SESSION_CAS_LOGOUTS],
            [
                [
                    ticket.service.name,
                    ticket.service.logout_url,
                    ticket.service.logout_use_iframe,
                    ticket.service.logout_use_iframe_timeout,
                ]
            ],
        )
        # Do not the same client for direct calls from the CAS service provider
        # to prevent use of the user session
        client = Client()
        ticket_id = urllib.parse.parse_qs(response.url.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/validate', {constants.TICKET_PARAM: ticket_id, constants.SERVICE_PARAM: self.URL}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/plain')
        self.assertEqual(force_str(response.content), 'yes\n%s\n' % self.LOGIN)
        # Verify ticket has been deleted
        with self.assertRaises(Ticket.DoesNotExist):
            Ticket.objects.get()

    def test_login_service_validate(self):
        response = self.client.get('/idp/cas/login/', {constants.SERVICE_PARAM: self.URL})
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get()
        location = response['Location']
        url = location.split('?')[0]
        query = urllib.parse.parse_qs(location.split('?')[1])
        self.assertTrue(url.endswith('/login/'))
        self.assertIn('nonce', query)
        self.assertIn('next', query)
        self.assertEqual(query['nonce'], [ticket.ticket_id])
        next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        self.assertEqual(next_url, '/idp/cas/continue/')
        self.assertEqual(set(next_url_query.keys()), {constants.SERVICE_PARAM, NONCE_FIELD_NAME})
        self.assertEqual(next_url_query[constants.SERVICE_PARAM], [self.URL])
        self.assertEqual(next_url_query[NONCE_FIELD_NAME], [ticket.ticket_id])
        response = self.client.get(location)
        response = self.client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        self.assertIn(AUTHENTICATION_EVENTS_SESSION_KEY, self.client.session)
        self.assertIn('nonce', self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0])
        self.assertIn(ticket.ticket_id, self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0]['nonce'])
        self.assertRedirectsComplex(response, query['next'][0], nonce=ticket.ticket_id)
        response = self.client.get(response.url)
        self.assertRedirectsComplex(response, self.URL, ticket=ticket.ticket_id)
        # Check logout state has been updated
        ticket = Ticket.objects.get()
        self.assertIn(constants.SESSION_CAS_LOGOUTS, self.client.session)
        self.assertEqual(
            self.client.session[constants.SESSION_CAS_LOGOUTS],
            [
                [
                    ticket.service.name,
                    ticket.service.logout_url,
                    ticket.service.logout_use_iframe,
                    ticket.service.logout_use_iframe_timeout,
                ]
            ],
        )
        # Do not the same client for direct calls from the CAS service provider
        # to prevent use of the user session
        client = Client()
        ticket_id = urllib.parse.parse_qs(response.url.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/serviceValidate', {constants.TICKET_PARAM: ticket_id, constants.SERVICE_PARAM: self.URL}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/xml')
        constraints = (
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:user', self.LOGIN),
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:attributes/cas:email', self.EMAIL),
        )
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Verify ticket has been deleted
        with self.assertRaises(Ticket.DoesNotExist):
            Ticket.objects.get()

    def test_login_service_validate_without_renew_failure(self):
        response = self.client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get()
        location = response['Location']
        url = location.split('?')[0]
        query = urllib.parse.parse_qs(location.split('?')[1])
        self.assertTrue(url.endswith('/login/'))
        self.assertIn('nonce', query)
        self.assertIn('next', query)
        self.assertEqual(query['nonce'], [ticket.ticket_id])
        next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        self.assertEqual(next_url, '/idp/cas/continue/')
        self.assertEqual(set(next_url_query.keys()), {constants.SERVICE_PARAM, NONCE_FIELD_NAME})
        self.assertEqual(next_url_query[constants.SERVICE_PARAM], [self.URL])
        self.assertEqual(next_url_query[NONCE_FIELD_NAME], [ticket.ticket_id])
        response = self.client.get(location)
        response = self.client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        self.assertIn(AUTHENTICATION_EVENTS_SESSION_KEY, self.client.session)
        self.assertIn('nonce', self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0])
        self.assertIn(ticket.ticket_id, self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0]['nonce'])
        self.assertRedirectsComplex(response, query['next'][0], nonce=ticket.ticket_id)
        response = self.client.get(response.url)
        self.assertRedirectsComplex(response, self.URL, ticket=ticket.ticket_id)
        # Check logout state has been updated
        ticket = Ticket.objects.get()
        self.assertIn(constants.SESSION_CAS_LOGOUTS, self.client.session)
        self.assertEqual(
            self.client.session[constants.SESSION_CAS_LOGOUTS],
            [
                [
                    ticket.service.name,
                    ticket.service.logout_url,
                    ticket.service.logout_use_iframe,
                    ticket.service.logout_use_iframe_timeout,
                ]
            ],
        )
        # Do not the same client for direct calls from the CAS service provider
        # to prevent use of the user session
        client = Client()
        ticket_id = urllib.parse.parse_qs(response.url.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/serviceValidate',
            {constants.TICKET_PARAM: ticket_id, constants.SERVICE_PARAM: self.URL, constants.RENEW_PARAM: ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/xml')
        constraints = (('/cas:serviceResponse/cas:authenticationFailure/@code', 'INVALID_TICKET_SPEC'),)
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Verify ticket has been deleted
        with self.assertRaises(Ticket.DoesNotExist):
            Ticket.objects.get()

    def test_login_proxy_validate_on_service_ticket(self):
        response = self.client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get()
        location = response['Location']
        url = location.split('?')[0]
        query = urllib.parse.parse_qs(location.split('?')[1])
        self.assertTrue(url.endswith('/login/'))
        self.assertIn('nonce', query)
        self.assertIn('next', query)
        self.assertEqual(query['nonce'], [ticket.ticket_id])
        next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        self.assertEqual(next_url, '/idp/cas/continue/')
        self.assertEqual(set(next_url_query.keys()), {constants.SERVICE_PARAM, NONCE_FIELD_NAME})
        self.assertEqual(next_url_query[constants.SERVICE_PARAM], [self.URL])
        self.assertEqual(next_url_query[NONCE_FIELD_NAME], [ticket.ticket_id])
        response = self.client.get(location)
        response = self.client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        self.assertIn(AUTHENTICATION_EVENTS_SESSION_KEY, self.client.session)
        self.assertIn('nonce', self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0])
        self.assertIn(ticket.ticket_id, self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0]['nonce'])
        self.assertRedirectsComplex(response, query['next'][0], nonce=ticket.ticket_id)
        response = self.client.get(response.url)
        self.assertRedirectsComplex(response, self.URL, ticket=ticket.ticket_id)
        # Check logout state has been updated
        ticket = Ticket.objects.get()
        self.assertIn(constants.SESSION_CAS_LOGOUTS, self.client.session)
        self.assertEqual(
            self.client.session[constants.SESSION_CAS_LOGOUTS],
            [
                [
                    ticket.service.name,
                    ticket.service.logout_url,
                    ticket.service.logout_use_iframe,
                    ticket.service.logout_use_iframe_timeout,
                ]
            ],
        )
        # Do not the same client for direct calls from the CAS service provider
        # to prevent use of the user session
        client = Client()
        ticket_id = urllib.parse.parse_qs(response.url.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/proxyValidate', {constants.TICKET_PARAM: ticket_id, constants.SERVICE_PARAM: self.URL}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/xml')
        constraints = (
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:user', self.LOGIN),
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:attributes/cas:email', self.EMAIL),
        )
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Verify ticket has been deleted
        with self.assertRaises(Ticket.DoesNotExist):
            Ticket.objects.get()

    @override_settings(A2_IDP_CAS_CHECK_PGT_URL=False)
    def test_proxy(self):
        response = self.client.get('/idp/cas/login', {constants.SERVICE_PARAM: self.URL})
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get()
        location = response['Location']
        url = location.split('?')[0]
        query = urllib.parse.parse_qs(location.split('?')[1])
        self.assertTrue(url.endswith('/login/'))
        self.assertIn('nonce', query)
        self.assertIn('next', query)
        self.assertEqual(query['nonce'], [ticket.ticket_id])
        next_url, next_url_query = query['next'][0].split('?')
        next_url_query = urllib.parse.parse_qs(next_url_query)
        self.assertEqual(next_url, '/idp/cas/continue/')
        self.assertEqual(set(next_url_query.keys()), {constants.SERVICE_PARAM, NONCE_FIELD_NAME})
        self.assertEqual(next_url_query[constants.SERVICE_PARAM], [self.URL])
        self.assertEqual(next_url_query[NONCE_FIELD_NAME], [ticket.ticket_id])
        response = self.client.get(location)
        response = self.client.post(
            location,
            {'login-password-submit': '', 'username': self.LOGIN, 'password': self.PASSWORD},
            follow=False,
        )
        self.assertIn(AUTHENTICATION_EVENTS_SESSION_KEY, self.client.session)
        self.assertIn('nonce', self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0])
        self.assertIn(ticket.ticket_id, self.client.session[AUTHENTICATION_EVENTS_SESSION_KEY][0]['nonce'])
        self.assertRedirectsComplex(response, query['next'][0], nonce=ticket.ticket_id)
        response = self.client.get(response.url)
        self.assertRedirectsComplex(response, self.URL, ticket=ticket.ticket_id)
        # Check logout state has been updated
        ticket = Ticket.objects.get()
        self.assertIn(constants.SESSION_CAS_LOGOUTS, self.client.session)
        self.assertEqual(
            self.client.session[constants.SESSION_CAS_LOGOUTS],
            [
                [
                    ticket.service.name,
                    ticket.service.logout_url,
                    ticket.service.logout_use_iframe,
                    ticket.service.logout_use_iframe_timeout,
                ]
            ],
        )
        # Do not the same client for direct calls from the CAS service provider
        # to prevent use of the user session
        client = Client()
        ticket_id = urllib.parse.parse_qs(response.url.split('?')[1])[constants.TICKET_PARAM][0]
        response = client.get(
            '/idp/cas/serviceValidate',
            {
                constants.TICKET_PARAM: ticket_id,
                constants.SERVICE_PARAM: self.URL,
                constants.PGT_URL_PARAM: self.PGT_URL,
            },
        )
        for key in client.session.keys():
            if key.startswith(constants.PGT_IOU_PREFIX):
                pgt_iou = key
                pgt = client.session[key]
                break
        else:
            # pylint: disable=redundant-unittest-assert
            self.assertTrue(False, 'PGTIOU- not found in session')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/xml')
        constraints = (
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:user', self.LOGIN),
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:proxyGrantingTicket', pgt_iou),
        )
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Verify service ticket has been deleted
        with self.assertRaises(Ticket.DoesNotExist):
            Ticket.objects.get(ticket_id=ticket_id)
        # Verify pgt ticket exists
        pgt_ticket = Ticket.objects.get(ticket_id=pgt)
        self.assertEqual(pgt_ticket.user, self.user)
        self.assertIsNone(pgt_ticket.expire)
        self.assertEqual(pgt_ticket.service, self.service)
        self.assertEqual(pgt_ticket.service_url, self.URL)
        self.assertEqual(pgt_ticket.proxies, self.PGT_URL)
        # Try to get a proxy ticket for service 2
        # it should fail since no proxy authorization exists
        client = Client()
        response = client.get(
            '/idp/cas/proxy', {constants.PGT_PARAM: pgt, constants.TARGET_SERVICE_PARAM: self.SERVICE2_URL}
        )
        constraints = (('/cas:serviceResponse/cas:proxyFailure/@code', 'PROXY_UNAUTHORIZED'),)
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Set proxy authorization
        self.service2.proxy.add(self.service)
        # Try again !
        response = client.get(
            '/idp/cas/proxy', {constants.PGT_PARAM: pgt, constants.TARGET_SERVICE_PARAM: self.SERVICE2_URL}
        )
        pt = Ticket.objects.get(ticket_id__startswith=constants.PT_PREFIX)
        self.assertEqual(pt.user, self.user)
        self.assertIsNotNone(pt.expire)
        self.assertEqual(pt.service, self.service2)
        self.assertEqual(pt.service_url, self.SERVICE2_URL)
        self.assertEqual(pt.proxies, self.PGT_URL)
        constraints = (('/cas:serviceResponse/cas:proxySuccess/cas:proxyTicket', pt.ticket_id),)
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Now service2 try to resolve the proxy ticket
        client = Client()
        response = client.get(
            '/idp/cas/proxyValidate',
            {constants.TICKET_PARAM: pt.ticket_id, constants.SERVICE_PARAM: self.SERVICE2_URL},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/xml')
        constraints = (
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:user', self.EMAIL),
            ('/cas:serviceResponse/cas:authenticationSuccess/cas:attributes/cas:username', self.LOGIN),
        )
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
        # Verify ticket has been deleted
        with self.assertRaises(Ticket.DoesNotExist):
            Ticket.objects.get(ticket_id=pt.ticket_id)
        # Check invalidation of PGT when session is closed
        self.client.logout()
        response = client.get(
            '/idp/cas/proxy', {constants.PGT_PARAM: pgt, constants.TARGET_SERVICE_PARAM: self.SERVICE2_URL}
        )
        constraints = (
            ('/cas:serviceResponse/cas:proxyFailure', 'session has expired'),
            ('/cas:serviceResponse/cas:proxyFailure/@code', 'BAD_PGT'),
        )
        self.assertXPathConstraints(response, constraints, CAS_NAMESPACES)
