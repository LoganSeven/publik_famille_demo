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

import logging
from collections import defaultdict
from datetime import timedelta
from xml.etree import ElementTree as ET

import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.timezone import now
from django.views.generic.base import View

from authentic2.attributes_ng.engine import get_attributes
from authentic2.constants import NONCE_FIELD_NAME
from authentic2.utils import hooks
from authentic2.utils.misc import (
    attribute_values_to_identifier,
    find_authentication_event,
    get_user_from_session_key,
    login_require,
    make_url,
    normalize_attribute_values,
    redirect,
)
from authentic2.utils.service import set_service
from authentic2.utils.view_decorators import enable_view_restriction
from authentic2.views import logout as logout_view
from authentic2_idp_cas.constants import (
    ATTRIBUTES_ELT,
    AUTHENTICATION_SUCCESS_ELT,
    BAD_PGT_ERROR,
    CANCEL_PARAM,
    CAS10_VALIDATION_FAILURE,
    CAS10_VALIDATION_SUCCESS,
    CAS20_PROXY_FAILURE,
    CAS20_VALIDATION_FAILURE,
    CAS_NAMESPACE,
    GATEWAY_PARAM,
    INTERNAL_ERROR,
    INVALID_REQUEST_ERROR,
    INVALID_SERVICE_ERROR,
    INVALID_TARGET_SERVICE_ERROR,
    INVALID_TICKET_ERROR,
    INVALID_TICKET_SPEC_ERROR,
    PGT_ELT,
    PGT_ID_PARAM,
    PGT_IOU_PARAM,
    PGT_IOU_PREFIX,
    PGT_PARAM,
    PGT_PREFIX,
    PGT_URL_PARAM,
    PROXIES_ELT,
    PROXY_ELT,
    PROXY_SUCCESS_ELT,
    PROXY_TICKET_ELT,
    PROXY_UNAUTHORIZED_ERROR,
    PT_PREFIX,
    RENEW_PARAM,
    SERVICE_PARAM,
    SERVICE_RESPONSE_ELT,
    SERVICE_TICKET_PREFIX,
    SESSION_CAS_LOGOUTS,
    TARGET_SERVICE_PARAM,
    TICKET_PARAM,
    USER_ELT,
)
from authentic2_idp_cas.models import Service, Ticket
from authentic2_idp_cas.utils import make_id

from . import app_settings

try:
    ET.register_namespace('cas', 'http://www.yale.edu/tp/cas')
except AttributeError:
    ET._namespace_map['http://www.yale.edu/tp/cas'] = 'cas'


class CasMixin:
    '''Common methods'''

    def __init__(self, *args, **kwargs):
        self.logger = logging.getLogger(__name__)

    def failure(self, request, service, reason):
        self.logger.warning('cas login from %r failed: %s', service, reason)
        if service:
            return redirect(request, service)
        else:
            return HttpResponseBadRequest(content=reason, content_type='text/plain')

    def redirect_to_service(self, request, st):
        if not st.valid():
            return self.failure(request, st.service_url, 'service ticket id is not valid')
        else:
            return self.return_ticket(request, st)

    def validate_ticket(self, request, st):
        if not st.service or not request.user.is_authenticated:
            return
        st.user = request.user
        st.validity = True
        st.expire = now() + timedelta(seconds=60)
        st.session_key = request.session.session_key
        st.save()
        if st.service.logout_url:
            request.session.setdefault(SESSION_CAS_LOGOUTS, []).append(
                (
                    st.service.name,
                    st.service.get_logout_url(request),
                    st.service.logout_use_iframe,
                    st.service.logout_use_iframe_timeout,
                )
            )

    def authenticate(self, request, st):
        """
        Redirect to an login page, pass a cookie to the login page to
        associate the login event with the service ticket, if renew was
        asked
        """
        nonce = st.ticket_id
        next_url = make_url(
            'a2-idp-cas-continue', params={SERVICE_PARAM: st.service_url, NONCE_FIELD_NAME: nonce}
        )
        return login_require(request, next_url=next_url, params={NONCE_FIELD_NAME: nonce})


class LoginView(CasMixin, View):
    http_method_names = ['get']

    def get(self, request):
        service = request.GET.get(SERVICE_PARAM)
        renew = request.GET.get(RENEW_PARAM) is not None
        gateway = request.GET.get(GATEWAY_PARAM) is not None

        if not service:
            return self.failure(request, '', 'no service field')
        model = Service.objects.for_service(service)
        if not model:
            return self.failure(request, service, 'service unknown')
        set_service(request, model)
        if renew and gateway:
            return self.failure(request, service, 'renew and gateway cannot be requested at the same time')

        hooks.call_hooks('event', name='sso-request', service=model)

        st = Ticket()
        st.service = model
        # Limit size of return URL to an acceptable length
        service = service[:4096]
        st.service_url = service
        st.renew = renew
        self.logger.debug('login request from %r renew: %s gateway: %s', service, renew, gateway)
        if self.must_authenticate(request, renew, gateway):
            st.save()
            return self.authenticate(request, st)

        # if user not authorized, a ServiceAccessDenied exception
        # is raised and handled by ServiceAccessMiddleware
        model.authorize(request.user)

        self.validate_ticket(request, st)
        if st.valid():
            st.save()
            hooks.call_hooks('event', name='sso-success', service=model, user=request.user)
            return redirect(request, service, params={'ticket': st.ticket_id})
        self.logger.debug('gateway requested but no session is open')
        return redirect(request, service)

    def must_authenticate(self, request, renew, gateway):
        """Does the user needs to authenticate ?"""
        return not gateway and (not request.user.is_authenticated or renew)


class ContinueView(CasMixin, View):
    http_method_names = ['get']

    def get(self, request):
        '''Continue CAS login after authentication'''
        service = request.GET.get(SERVICE_PARAM)
        ticket_id = request.GET.get(NONCE_FIELD_NAME)
        cancel = request.GET.get(CANCEL_PARAM) is not None
        if ticket_id is None:
            return self.failure(request, service, 'missing ticket id')
        if not ticket_id.startswith(SERVICE_TICKET_PREFIX):
            return self.failure(request, service, 'invalid ticket id')
        try:
            st = Ticket.objects.select_related('service', 'user').get(ticket_id=ticket_id)
        except Ticket.DoesNotExist:
            return self.failure(request, service, 'unknown ticket id')
        # no valid ticket should be submitted to continue, delete them !
        if st.valid():
            st.delete()
            return self.failure(request, service, 'ticket %r already valid passed to continue' % st.ticket_id)
        # service URL mismatch
        if st.service_url != service:
            st.delete()
            return self.failure(request, service, 'ticket service does not match service parameter')
        # user asked for cancellation
        if cancel:
            st.delete()
            self.logger.debug('login from %s canceled', service)
            return redirect(request, service)
        # Not logged in ? Authenticate again
        if not request.user.is_authenticated:
            return self.authenticate(request, st)
        # Renew requested and ticket is unknown ? Try again
        if st.renew and not find_authentication_event(request, st.ticket_id):
            return self.authenticate(request, st)
        # if user not authorized, a ServiceAccessDenied exception
        # is raised and handled by ServiceAccessMiddleware
        st.service.authorize(request.user)

        self.validate_ticket(request, st)
        if st.valid():
            hooks.call_hooks('event', name='sso-success', service=st.service, user=st.user)
            return redirect(request, service, params={'ticket': st.ticket_id})
        # Should not happen
        assert False


class ValidateBaseView(CasMixin, View):
    http_method_names = ['get']
    prefixes = [SERVICE_TICKET_PREFIX]

    def get(self, request):
        try:
            service = request.GET.get(SERVICE_PARAM)
            ticket = request.GET.get(TICKET_PARAM)
            renew = request.GET.get(RENEW_PARAM) is not None
            if service is None:
                return self.failure(request, service, 'service parameter is missing')
            if ticket is None:
                return self.validation_failure(request, service, INVALID_REQUEST_ERROR)
            self.logger.debug('validation service: %r ticket: %r renew: %s', service, ticket, renew)
            if not ticket.split('-')[0] + '-' in self.prefixes:
                return self.validation_failure(request, service, INVALID_TICKET_SPEC_ERROR)
            model = Service.objects.for_service(service)
            if not model:
                return self.validation_failure(request, service, INVALID_SERVICE_ERROR)
            try:
                st = Ticket.objects.get(ticket_id=ticket)
            except Ticket.DoesNotExist:
                st = None
            else:
                st.delete()

            if st is None:
                return self.validation_failure(request, service, INVALID_TICKET_ERROR)
            if service != st.service_url:
                return self.validation_failure(request, service, INVALID_SERVICE_ERROR)
            if not st.valid() or renew and not st.renew:
                return self.validation_failure(request, service, INVALID_TICKET_SPEC_ERROR)
            attributes = self.get_attributes(request, st)
            if attributes is None:
                return self.validation_failure(request, service, INVALID_TICKET_ERROR)
            if st.service.identifier_attribute not in attributes:
                self.logger.error(
                    'unable to compute an identifier for user %r and service %s',
                    str(st.user),
                    st.service_url,
                )
                return self.validation_failure(request, service, INTERNAL_ERROR)
            # Compute user identifier
            identifier = attribute_values_to_identifier(attributes[st.service.identifier_attribute])
            return self.validation_success(request, st, identifier)
        except Exception:
            self.logger.exception('internal server error')
            return self.validation_failure(request, service, INTERNAL_ERROR)

    def get_attributes(self, request, st):
        '''Retrieve attribute for users of the session linked to the ticket'''
        if not hasattr(st, 'attributes'):
            wanted_attributes = st.service.get_wanted_attributes()
            # use from session can be an LDAPUser with special attributes
            user = get_user_from_session_key(st.session_key)
            if not user.pk:  # anonymous user, fail
                return None
            if user.pk != st.user_id:
                return None  # user has changed, fail
            attributes = get_attributes(
                {
                    'request': request,
                    'user': user,
                    'service': st.service,
                    '__wanted_attributes': wanted_attributes,
                }
            )
        return attributes

    def validation_failure(self, request, service, code):
        self.logger.warning('validation failed service: %r code: %s', service, code)
        return self.real_validation_failure(request, service, code)

    def validation_success(self, request, st, identifier):
        self.logger.info(
            'validation success service: %r ticket: %s user: %r identifier: %r',
            st.service_url,
            st.ticket_id,
            str(st.user),
            identifier,
        )
        return self.real_validation_success(request, st, identifier)


class ValidateView(ValidateBaseView):
    def real_validation_failure(self, request, service, code):
        return HttpResponse(CAS10_VALIDATION_FAILURE, content_type='text/plain')

    def real_validation_success(self, request, st, identifier):
        return HttpResponse(CAS10_VALIDATION_SUCCESS % identifier, content_type='text/plain')


class ServiceValidateView(ValidateBaseView):
    add_proxies = False

    def real_validation_failure(self, request, service, code, message=''):
        message = message or self.get_cas20_error_message(code)
        return HttpResponse(CAS20_VALIDATION_FAILURE % (code, message), content_type='text/xml')

    def get_cas20_error_message(self, code):
        return ''  # FIXME

    def real_validation_success(self, request, st, identifier):
        root = ET.Element(SERVICE_RESPONSE_ELT)
        success = ET.SubElement(root, AUTHENTICATION_SUCCESS_ELT)
        user = ET.SubElement(success, USER_ELT)
        user.text = str(identifier)
        self.provision_pgt(request, st, success)
        self.provision_attributes(request, st, success)
        return HttpResponse(ET.tostring(root, encoding='utf-8'), content_type='text/xml')

    def provision_attributes(self, request, st, success):
        '''Add attributes to the CAS 2.0 ticket'''
        values = defaultdict(set)
        ctx = self.get_attributes(request, st)
        for attribute in st.service.attribute_set.all():
            if not attribute.enabled:
                continue
            slug = attribute.slug
            name = attribute.attribute_name
            if name in ctx:
                normalized = normalize_attribute_values(ctx[name])
                values[slug].update(normalized)
        if values:
            attributes_elt = ET.SubElement(success, ATTRIBUTES_ELT)
        for key, values in values.items():
            for value in values:
                attribute_elt = ET.SubElement(attributes_elt, '{%s}%s' % (CAS_NAMESPACE, key))
                attribute_elt.text = str(value)

    def provision_pgt(self, request, st, success):
        """Provision a PGT ticket if requested"""
        pgt_url = request.GET.get(PGT_URL_PARAM)
        if not pgt_url:
            return
        if not pgt_url.startswith('https://'):
            self.logger.warning('ignoring non HTTP pgtUrl %r', pgt_url)
            return
        # PGT URL must be declared
        if not st.service.match_service(pgt_url):
            self.logger.warning('pgtUrl %r does not match service %r', pgt_url, st.service.slug)
        pgt = make_id(PGT_PREFIX)
        pgt_iou = make_id(PGT_IOU_PREFIX)
        # Skip PGT_URL check for testing purpose
        # instead store PGT_IOU / PGT association in session
        if app_settings.CHECK_PGT_URL:
            response = requests.get(
                pgt_url, params={PGT_ID_PARAM: pgt, PGT_IOU_PARAM: pgt_iou}, timeout=settings.REQUESTS_TIMEOUT
            )
            if response.status_code != 200:
                self.logger.warning('pgtUrl %r returned non 200 code: %d', pgt_url, response.status_code)
                return
        else:
            request.session[pgt_iou] = pgt
        proxies = ('%s %s' % (pgt_url, st.proxies)).strip()
        # Save the PGT ticket
        Ticket.objects.create(
            ticket_id=pgt,
            expire=None,
            service=st.service,
            service_url=st.service_url,
            validity=True,
            user=st.user,
            session_key=st.session_key,
            proxies=proxies,
        )
        user = ET.SubElement(success, PGT_ELT)
        user.text = pgt_iou
        if self.add_proxies:
            proxies_elt = ET.SubElement(success, PROXIES_ELT)
            for proxy in st.proxies.split():
                proxy_elt = ET.SubElement(proxies_elt, PROXY_ELT)
                proxy_elt.text = proxy


class ProxyView(View):
    http_method_names = ['get']

    def get(self, request):
        pgt = request.GET.get(PGT_PARAM)
        target_service_url = request.GET.get(TARGET_SERVICE_PARAM)
        if not pgt or not target_service_url:
            return self.validation_failure(
                INVALID_REQUEST_ERROR, "'pgt' and 'targetService' parameters are both required"
            )
        if not pgt.startswith(PGT_PREFIX):
            return self.validation_failure(BAD_PGT_ERROR, 'a proxy granting ticket must start with PGT-')
        try:
            pgt = Ticket.objects.get(ticket_id=pgt)
        except Ticket.DoesNotExist:
            pgt = None
        if pgt is None:
            return self.validation_failure(BAD_PGT_ERROR, 'pgt does not exist')
        if not pgt.valid():
            pgt.delete()
            return self.validation_failure(BAD_PGT_ERROR, 'session has expired')
        target_service = Service.objects.for_service(target_service_url)
        # No target service exists for this url, maybe the URL is missing from
        # the urls field
        if not target_service:
            return self.validation_failure(INVALID_TARGET_SERVICE_ERROR, 'target service is invalid')
        # Verify that the requested service is authorized to get proxy tickets
        # for the target service
        if not target_service.proxy.filter(pk=pgt.service_id).exists():
            return self.validation_failure(
                PROXY_UNAUTHORIZED_ERROR, 'proxying to the target service is forbidden'
            )
        pt = Ticket.objects.create(
            ticket_id=make_id(PT_PREFIX),
            validity=True,
            expire=now() + timedelta(seconds=60),
            service=target_service,
            service_url=target_service_url,
            user=pgt.user,
            session_key=pgt.session_key,
            proxies=pgt.proxies,
        )
        return self.validation_success(request, pt)

    def validation_failure(self, code, reason):
        return HttpResponse(CAS20_PROXY_FAILURE % (code, reason), content_type='text/xml')

    def validation_success(self, request, pt):
        root = ET.Element(SERVICE_RESPONSE_ELT)
        success = ET.SubElement(root, PROXY_SUCCESS_ELT)
        proxy_ticket = ET.SubElement(success, PROXY_TICKET_ELT)
        proxy_ticket.text = pt.ticket_id
        return HttpResponse(ET.tostring(root, encoding='utf-8'), content_type='text/xml')


class ProxyValidateView(ServiceValidateView):
    http_method_names = ['get']
    prefixes = [SERVICE_TICKET_PREFIX, PT_PREFIX]
    add_proxies = True


class LogoutView(View):
    http_method_names = ['get']

    def get(self, request):
        referrer = request.headers.get('Referer')
        next_url = request.GET.get('service') or make_url('auth_homepage')
        if referrer:
            model = Service.objects.for_service(referrer)
            if model:
                set_service(request, model)
                return logout_view(request, next_url=next_url, check_referer=False, do_local=False)
        return redirect(request, next_url)


login = enable_view_restriction(LoginView.as_view())
logout = LogoutView.as_view()
_continue = enable_view_restriction(ContinueView.as_view())
validate = ValidateView.as_view()
service_validate = ServiceValidateView.as_view()
proxy = ProxyView.as_view()
proxy_validate = ProxyValidateView.as_view()
