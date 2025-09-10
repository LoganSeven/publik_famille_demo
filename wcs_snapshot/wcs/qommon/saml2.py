# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import datetime
import time
import urllib.parse
from xml.sax.saxutils import escape

try:
    import lasso
except ImportError:
    lasso = None

from django.utils.encoding import force_str
from quixote import (
    get_field,
    get_publisher,
    get_request,
    get_response,
    get_session,
    get_session_manager,
    redirect,
)
from quixote.directory import Directory
from quixote.http_request import parse_header

from wcs import sql

from . import _, errors, force_str, misc
from .publisher import get_cfg, get_logger
from .template import QommonTemplateResponse, error_page


class SOAPException(Exception):
    url = None

    def __init__(self, url=None):
        self.url = url


def soap_call(url, msg):
    try:
        dummy, status, data, dummy = misc.http_post_request(url, msg, headers={'Content-Type': 'text/xml'})
    except errors.ConnectionError as err:
        # exception could be raised by request
        get_logger().warning('SOAP error (on %s): %s' % (url, err))
        raise SOAPException(url)
    if status not in (200, 204):  # 204 ok for federation termination
        get_logger().warning('SOAP error (%s) (on %s)' % (status, url))
        raise SOAPException(url)
    return data


def soap_endpoint(method):
    def f(*args, **kwargs):
        if get_request().get_method() != 'POST':
            raise errors.TraversalError()
        response = get_response()
        response.set_content_type('text/xml', 'utf-8')
        try:
            return method(*args, **kwargs)
        except Exception as e:
            get_publisher().record_error(
                _('Exception in method %r: returning a SOAP error') % method, exception=e
            )
            fault = lasso.SoapFault.newFull('Internal Server Error', str(e))
            body = lasso.SoapBody()
            body.any = [fault]
            envelope = lasso.SoapEnvelope(body)
            return envelope.exportToXml()

    return f


def saml2_status_summary(response):
    if not response.status or not response.status.statusCode:
        return 'No status or status code'
    code = response.status.statusCode.value
    if response.status.statusCode.statusCode:
        code += ':' + response.status.statusCode.statusCode.value
    return code


def get_remote_provider_cfg(profile):
    '''Lookup the configuration for a remote provider given a profile'''
    remote_provider_key = misc.get_provider_key(profile.remoteProviderId)
    return get_cfg('idp', {}).get(remote_provider_key)


class Saml2Directory(Directory):
    _q_exports = [
        'login',
        'singleSignOnArtifact',
        'singleSignOnPost',
        'singleSignOnRedirect',
        'assertionConsumerArtifact',
        'assertionConsumerPost',
        'assertionConsumerRedirect',
        'singleLogout',
        'singleLogoutReturn',
        'singleLogoutSOAP',
        'metadata',
        ('metadata.xml', 'metadata'),
        'public_key',
        'error',
    ]

    def _q_traverse(self, path):
        # if lasso is not installed, hide the saml endpoints
        if lasso is None:
            raise errors.TraversalError()
        return Directory._q_traverse(self, path)

    def log_profile_error(self, profile, error, what):
        get_logger().info(
            '%(what)s from %(provider)s failed: %(message)s'
            % {'provider': profile.remoteProviderId, 'message': error[1], 'what': what}
        )

    def postForm(self, profile=None, url=None, body=None):
        if profile:
            url = profile.msgUrl
            body = profile.msgBody
        # XXX: translate message in the form
        return """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
  <title>Authentication Request</title>
 </head>
 <body onload="document.forms[0].submit()">
  <h1>Authentication Request</h1>
  <form action="%(url)s" method="post">
   <p>You should be automaticaly redirected to the identity provider.</p>
   <p>If this page is still visible after a few seconds, press the <em>Send</em> button below.</p>
   <input type="hidden" name="SAMLRequest" value="%(body)s" />
   <div class="buttons-bar">
    <button>Send</button>
   </div>
  </form>
 </body>
</html>
""" % {
            'url': url,
            'body': body,
        }

    def login(self):
        return self.perform_login()

    def perform_login(self, idp=None, relay_state=None):
        get_response().set_robots_no_index()
        server = misc.get_lasso_server()
        if not server:
            return error_page(_('SAML 2.0 support not yet configured.'))
        login = lasso.Login(server)
        login.initAuthnRequest(idp, lasso.HTTP_METHOD_REDIRECT)
        idp_options = get_remote_provider_cfg(login)
        if idp_options.get('nameidformat') == 'unspecified':
            login.request.nameIDPolicy.format = lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED
        elif idp_options.get('nameidformat') == 'email':
            login.request.nameIDPolicy.format = lasso.SAML2_NAME_IDENTIFIER_FORMAT_EMAIL
        else:
            login.request.nameIDPolicy.format = lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT
        login.request.nameIDPolicy.allowCreate = True
        login.request.forceAuthn = get_request().form.get('forceAuthn') == 'true'
        login.request.isPassive = get_request().form.get('IsPassive') == 'true'
        login.request.consent = 'urn:oasis:names:tc:SAML:2.0:consent:current-implicit'

        if not relay_state and isinstance(get_request().form.get('next'), str):
            relay_state = get_request().form.get('next')
        if relay_state:
            login.msgRelayState = relay_state

        next_url = relay_state or get_publisher().get_frontoffice_url()
        if not get_publisher().is_relatable_url(next_url):
            return error_page(_('Invalid URL in RelayState'))
        next_url = urllib.parse.urljoin(get_publisher().get_frontoffice_url(), next_url)
        samlp_extensions = '''<samlp:Extensions
                        xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                        xmlns:eo="https://www.entrouvert.com/">
                      <eo:next_url>%s</eo:next_url>''' % escape(
            next_url
        )

        if not get_publisher().has_site_option('disable-saml-login-hint'):
            # set login-hint only if backoffice is accessed
            if next_url.startswith(get_publisher().get_backoffice_url()):
                samlp_extensions += '<eo:login-hint>backoffice</eo:login-hint>'
        samlp_extensions += '</samlp:Extensions>'
        login.request.extensions = lasso.Node.newFromXmlNode(samlp_extensions)
        login.buildAuthnRequestMsg()
        return redirect(login.msgUrl)

    def assertionConsumerArtifact(self):
        get_response().set_robots_no_index()
        server = misc.get_lasso_server()
        if not server:
            return error_page(_('SAML 2.0 support not yet configured.'))
        login = lasso.Login(server)
        request = get_request()
        try:
            if request.get_method() == 'GET':
                message, method = request.get_query(), lasso.HTTP_METHOD_ARTIFACT_GET
            elif request.get_method() == 'POST':
                message, method = request.form.get('SAMLart', None), lasso.HTTP_METHOD_ARTIFACT_POST
            elif request.get_method() == 'HEAD':
                # A proper HEAD response would be a redirection but that would mean
                # contacting the identify provider and probably it will mark the
                # artifact as consumed and that would break the GET request that is
                # to come. Hence not doing anything.
                return ''
            else:
                get_logger().info('Bad HTTP method on assertionConsumerArtifact endpoint')
                return error_page(_('Invalid authentication response'))
            login.initRequest(force_str(message), method)
        except lasso.Error as error:
            self.log_profile_error(login, error, 'login.initRequest')
            return error_page(_('Invalid authentication response'))

        login.buildRequestMsg()

        try:
            soap_answer = soap_call(login.msgUrl, login.msgBody)
        except SOAPException:
            relay_state = request.form.get('RelayState', None)
            path = '/saml/error'
            if relay_state:
                path += '?RelayState=' + urllib.parse.quote(relay_state)
            return redirect(path)

        try:
            login.processResponseMsg(force_str(soap_answer))
        except lasso.Error as error:
            return self.assertion_consumer_process_response_error(login, error)
        return self.sso_after_response(login)

    def assertion_consumer_process_response_error(self, login, error):
        if isinstance(error, lasso.DsError):
            message = _('Signature verification failed')
        elif error[0] in (lasso.LOGIN_ERROR_STATUS_NOT_SUCCESS, lasso.PROFILE_ERROR_STATUS_NOT_SUCCESS):
            try:
                # Passive login failed, just continue
                if login.response.status.statusCode.statusCode.value == lasso.SAML2_STATUS_CODE_NO_PASSIVE:
                    return self.continue_to_after_url()
                # if error code is request denied, it's probably because
                # the user pressed 'cancel' on the identity provider
                # authentication form.
                if (
                    login.response.status.statusCode.statusCode.value
                    == lasso.SAML2_STATUS_CODE_REQUEST_DENIED
                ):
                    return redirect(get_publisher().get_root_url())
            except AttributeError:
                pass
            message = _('Authentication failure %s') % saml2_status_summary(login.response)
        elif error[0] == lasso.SERVER_ERROR_PROVIDER_NOT_FOUND:
            message = _('Request from unknown provider ID')
        elif error[0] == lasso.LOGIN_ERROR_UNKNOWN_PRINCIPAL:
            message = _('Authentication failure; unknown principal')
        elif error[0] == lasso.LOGIN_ERROR_FEDERATION_NOT_FOUND:
            message = _('Authentication failure; federation not found')
        elif error[0] == lasso.PROFILE_ERROR_MISSING_RESPONSE:
            message = _('Authentication failure; failed to get response')
        else:
            message = _('Unknown error')
        self.log_profile_error(login, error, 'assertion consumer error: %r' % message)
        return error_page(message)

    def sso_after_response(self, login):
        try:
            assertion = login.response.assertion[0]
            last_slash = get_request().get_url().rfind('/')
            if (
                assertion.subject.subjectConfirmation.subjectConfirmationData.recipient
                != get_cfg('sp', {}).get('saml2_base_url') + get_request().get_url()[last_slash:]
            ):
                return error_page('SubjectConfirmation Recipient Mismatch')
        except Exception as e:
            get_publisher().record_error(exception=e, context='SAML', notify=True)
            return error_page('Error checking SubjectConfirmation Recipient')

        try:
            current_time = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            not_before = assertion.subject.subjectConfirmation.subjectConfirmationData.notBefore
            not_on_or_after = assertion.subject.subjectConfirmation.subjectConfirmationData.notOnOrAfter
            if not_before and current_time < not_before:
                return error_page('Assertion received too early')
            if not_on_or_after and current_time > not_on_or_after:
                return error_page('Assertion expired')
        except Exception as e:
            get_publisher().record_error(exception=e, context='SAML', notify=True)
            return error_page('Error checking Assertion Time')

        expiration_time = not_on_or_after
        if not expiration_time:
            expiration_time = datetime.now() + datetime.timedelta(days=30)
        if not sql.UsedSamlAssertionId.consume_assertion_id(assertion.iD, expiration_time):
            return error_page('Assertion replay')

        try:
            if assertion.subject.subjectConfirmation.method != 'urn:oasis:names:tc:SAML:2.0:cm:bearer':
                return error_page('Unknown SubjectConfirmation Method')
        except Exception:
            return error_page('Error checking SubjectConfirmation Method')

        try:
            audience_ok = False
            for audience_restriction in assertion.conditions.audienceRestriction:
                if audience_restriction.audience != login.server.providerId:
                    return error_page('Incorrect AudienceRestriction')
                audience_ok = True
            if not audience_ok:
                return error_page('Incorrect AudienceRestriction')
        except Exception:
            return error_page('Error checking AudienceRestriction')

        # TODO: check for unknown conditions

        login.acceptSso()
        session = get_session()
        if login.isSessionDirty:
            if login.session:
                session.lasso_session_dump = login.session.dump()
            else:
                session.lasso_session_dump = None

        if (
            assertion.authnStatement[0].authnContext
            and assertion.authnStatement[0].authnContext.authnContextClassRef
        ):
            session.saml_authn_context = assertion.authnStatement[0].authnContext.authnContextClassRef
        if assertion.authnStatement[0].sessionIndex:
            session.lasso_session_index = assertion.authnStatement[0].sessionIndex

        if assertion.authnStatement[0].sessionNotOnOrAfter:
            try:
                t = misc.parse_isotime(assertion.authnStatement[0].sessionNotOnOrAfter)
            except ValueError:
                return error_page('Error extracting SessionNotOnOrAfter')
            session.set_expire(t)

        user = self.lookup_user(session, login)
        if user:
            session.set_user(user.id)
            # save value of idp_session_cookie_name for wcs.root.RootDirectory.try_passive_sso()
            idp_session_cookie_name = get_publisher().get_site_option('idp_session_cookie_name')
            if idp_session_cookie_name:
                if idp_session_cookie_name in get_request().cookies:
                    session.opened_session_value = get_request().cookies[idp_session_cookie_name]
        else:
            return error_page('Error associating user on SSO')
        session.lasso_identity_provider_id = login.remoteProviderId
        session.message = None
        return self.continue_to_after_url()

    def continue_to_after_url(self):
        request = get_request()
        relay_state = request.form.get('RelayState', None)
        response = get_response()
        if relay_state == 'backoffice':
            after_url = get_publisher().get_backoffice_url()
        elif relay_state:
            if not get_publisher().is_relatable_url(relay_state):
                return error_page(_('Invalid URL in RelayState'))
            after_url = urllib.parse.urljoin(get_publisher().get_frontoffice_url(), relay_state)
        else:
            after_url = get_publisher().get_frontoffice_url()
        response.set_status(303)
        response.headers['location'] = after_url
        response.content_type = 'text/plain'
        return 'Your browser should redirect you'

    def assertionConsumerPost(self):
        message = get_field('SAMLResponse')
        if not message:
            return error_page(_('No SAML Response'))
        return self.assertion_consumer_process(message)

    def assertionConsumerRedirect(self):
        query_string = get_request().get_query()
        if not query_string:
            return error_page(_('No SAML Response in query string'))
        return self.assertion_consumer_process(query_string)

    def assertion_consumer_process(self, message):
        server = misc.get_lasso_server()
        if not server:
            return error_page(_('SAML 2.0 support not yet configured.'))
        login = lasso.Login(server)
        try:
            login.processAuthnResponseMsg(message)
        except lasso.Error as error:
            return self.assertion_consumer_process_response_error(login, error)
        return self.sso_after_response(login)

    def fill_user_attributes(self, session, login, user):
        '''Fill user fields from SAML2 assertion attributes'''
        logger = get_logger()

        save = False
        idp = get_remote_provider_cfg(login)
        # lookup for attributes in assertion and automatically create identity
        lasso_session = lasso.Session.newFromDump(session.lasso_session_dump)
        try:
            assertion = lasso_session.getAssertions(None)[0]
        except Exception:
            get_logger().warning('no assertion')
            return None

        d = {}
        m = {}
        try:
            for attribute in assertion.attributeStatement[0].attribute:
                # always mark the attribute as being present, even if it won't
                # have any value, as an empty value (role-slug) must not be
                # ignored.
                m.setdefault(attribute.name, [])
                try:
                    d[attribute.name] = attribute.attributeValue[0].any[0].content
                    for attribute_value in attribute.attributeValue:
                        l = m[attribute.name]
                        l.append(attribute_value.any[0].content)
                except IndexError:
                    pass
        except IndexError:
            pass
        logger.debug('fill_user_attributes: received attributes %r', m)
        admin_attributes = idp.get('admin-attributes', {'local-admin': 'true'}) or {}
        if admin_attributes:
            is_admin = False
            for key, matching_value in admin_attributes.items():
                for value in m.get(key, []):
                    if value == matching_value:
                        is_admin = True
            if user.is_admin != is_admin:
                user.is_admin = is_admin
                if user.is_admin:
                    logger.info('giving user %s the admin rights', user.id)
                else:
                    logger.info('taking user %s the admin rights', user.id)
                save = True
        attribute_mapping = idp.get('attribute-mapping') or {}

        from wcs.admin.settings import UserFieldsFormDef

        formdef = UserFieldsFormDef(publisher=get_publisher())
        if formdef:
            dict_fields = {x.id: x for x in formdef.fields}
        else:
            dict_fields = {}

        if user.form_data is None:
            user.form_data = {}
        for key, field_id in attribute_mapping.items():
            if key not in d:
                continue
            field_value = d[key]
            field = dict_fields.get(field_id)
            if field and field.convert_value_from_anything:
                try:
                    field_value = field.convert_value_from_anything(field_value)
                except ValueError as e:
                    get_publisher().record_error(exception=e, context='SAML', notify=True)
                    continue
            if user.form_data.get(field_id) != field_value:
                user.form_data[field_id] = field_value
                logger.info('setting field %s of user %s to value %r', field_id, user.id, field_value)
                save = True

        # update user roles from role-slug
        if 'role-slug' in m:
            role_ids = []
            names = []
            # uuid are in a role-slug attribute, it's historical, as at some
            # point roles in authentic where provisionned from w.c.s. and join
            # was done on the slug field.
            for uuid in m['role-slug']:
                role = get_publisher().role_class.resolve(uuid)
                if not role:
                    logger.warning('role uuid %s is unknown', uuid)
                    continue
                role_ids.append(force_str(role.id))
                names.append(force_str(role.name))
            if set(user.roles or []) != set(role_ids):
                user.roles = role_ids
                logger.info('enrolling user %s in %s', user.id, ', '.join(names))
                save = True

        # verified attributes
        verified_attributes = m.get('verified_attributes')
        if verified_attributes is not None:
            if verified_attributes:
                # If there are any verified attributes we consider
                # first and last names are also verified.  This is to work
                # around the fact that those attributes are handled
                # differently in authentic and cannot be marked as
                # verified.
                verified_attributes.extend(['first_name', 'last_name'])
            verified_fields = []
            verified_fieldnames = []
            if user.get_formdef() and user.get_formdef().fields:
                for field in user.get_formdef().fields:
                    if field.varname in verified_attributes:
                        verified_fields.append(field.id)
                        verified_fieldnames.append(field.varname)
            if set(user.verified_fields or []) != set(verified_fields):
                user.verified_fields = verified_fields
                logger.info('verified attributes for user %s are %s', user.id, ', '.join(verified_fieldnames))
                save = True

        if save:
            user.store()

    def lookup_user(self, session, login):
        if not login.nameIdentifier or not login.nameIdentifier.content:
            return None
        user_class = get_publisher().user_class
        ni = login.nameIdentifier.content
        session.name_identifier = ni
        while True:
            users = sorted(
                user_class.get_users_with_name_identifier(ni), key=lambda u: (u.last_seen or 0, -int(u.id))
            )
            if users:
                # if multiple users, use the more recently used or the younger
                user = users[-1]
            else:
                user = get_publisher().user_class(ni)
                user.name_identifiers = [ni]
                if login.identity:
                    user.lasso_dump = login.identity.dump()
                user.store()

                others = user_class.get_users_with_name_identifier(ni)
                # there is an user mapping to the same id with a younger id:
                # try again.
                if any(int(other.id) < int(user.id) for other in others):
                    user.remove_self()
                    continue
            break

        self.fill_user_attributes(session, login, user)

        if user.form_data:
            user.set_attributes_from_formdata(user.form_data)

        if not user.name:
            # we didn't get useful attributes, forget it.
            get_logger().warning('failed to get useful attributes from the assertion')
            return None

        user.store()
        return user

    def slo_sp(self, method=None):
        if method is None:
            method = lasso.HTTP_METHOD_REDIRECT

        logout = lasso.Logout(misc.get_lasso_server())
        session = get_session()

        if session.lasso_session_dump:
            logout.setSessionFromDump(session.lasso_session_dump)

        user = get_request().user
        if user and user.lasso_dump:
            logout.setIdentityFromDump(user.lasso_dump)
        session.user = None

        if method == lasso.HTTP_METHOD_REDIRECT:
            return self.slo_sp_redirect(logout)

        if method == lasso.HTTP_METHOD_SOAP:
            return self.slo_sp_soap(logout)

    def slo_sp_redirect(self, logout):
        session = get_session()
        try:
            logout.initRequest(None, lasso.HTTP_METHOD_REDIRECT)
        except lasso.Error as error:
            self.log_profile_error(logout, error, 'logout.initRequest')
            get_session_manager().expire_session()
            return redirect(get_publisher().get_root_url())
        if session.lasso_session_index:
            logout.request.sessionIndex = session.lasso_session_index
        logout.buildRequestMsg()
        return redirect(logout.msgUrl)

    def singleLogoutReturn(self):
        logout = lasso.Logout(misc.get_lasso_server())
        if get_session().lasso_session_dump:
            logout.setSessionFromDump(get_session().lasso_session_dump)
        message = get_request().get_query()
        return self.slo_return(logout, message)

    def slo_return(self, logout, message):
        session = get_session()

        try:
            logout.processResponseMsg(force_str(message))
        except lasso.Error as error:
            self.log_profile_error(logout, error, 'logout.processResponseMsg')
            if error[0] == lasso.LOGOUT_ERROR_UNKNOWN_PRINCIPAL:
                get_logger().warning('Unknown principal on logout, probably session stopped already on IdP')
                # XXX: wouldn't work when logged on two IdP
                session.lasso_session_dump = None
        else:
            get_logger().info('Successful logout from %s' % logout.remoteProviderId)
            if logout.isSessionDirty:
                if logout.session:
                    session.lasso_session_dump = logout.session.dump()
                else:
                    session.lasso_session_dump = None

            get_session_manager().expire_session()
        return redirect(get_publisher().get_root_url())

    def slo_sp_soap(self, logout):
        try:
            logout.initRequest(None, lasso.HTTP_METHOD_SOAP)
        except lasso.Error as error:
            self.log_profile_error(logout, error, 'logout.initRequest SOAP')
            get_session_manager().expire_session()
            get_session().add_message(_('Could not send logout request to the identity provider'))
            return redirect(get_publisher().get_root_url())

        logout.buildRequestMsg()
        try:
            soap_answer = soap_call(logout.msgUrl, logout.msgBody)
        except SOAPException:
            return error_page(_('Failure to communicate with identity provider'))

        return self.slo_return(logout, soap_answer)

    def get_soap_message(self):
        request = get_request()
        ctype = request.environ.get('CONTENT_TYPE')
        if not ctype:
            get_logger().warning('SOAP Endpoint got message without content-type')
            raise SOAPException()

        ctype = parse_header(ctype)[0]
        if ctype not in ('text/xml', 'application/vnd.paos+xml'):
            get_logger().warning('SOAP Endpoint got message with wrong content-type (%s)' % ctype)
            raise SOAPException()

        length = int(request.environ.get('CONTENT_LENGTH'))
        return request.stdin.read(length)

    #
    # SLO IdP Section
    #
    def kill_sessions(self, nameid, session_indexes=(), not_current_session=False):
        if not not_current_session:
            get_session_manager().expire_session()
        # session has not been found, this may be because the user has
        # its browser configured so that cookies are not sent for
        # remote queries and IdP is using image-based SLO.
        # so we look up a session with the appropriate name identifier
        sessions = get_session_manager().get_sessions_for_saml(nameid.content, session_indexes)
        session_manager = get_session_manager()
        for session in sessions:
            if session.id:
                id = session.id
                session.id = None
                try:
                    del session_manager[id]
                except KeyError:
                    pass

    def singleLogoutPOST(self):
        message = get_field('SAMLRequest')
        return self.slo_idp(message)

    @soap_endpoint
    def singleLogoutSOAP(self):
        try:
            soap_message = self.get_soap_message()
        except Exception:
            return
        return self.slo_idp(soap_message, soap=True)

    def singleLogout(self):
        return self.slo_idp(get_request().get_query())

    def slo_idp(self, message, soap=False):
        logout = lasso.Logout(misc.get_lasso_server())
        try:
            logout.processRequestMsg(force_str(message))
        except lasso.Error as error:
            # XXX: add option to ignore signature errors for a specific sp
            self.log_profile_error(logout, error, 'logout.processRequestMsg')
            return self.slo_idp_finish(logout, soap)

        try:
            sessions = get_session_manager().get_sessions_for_saml(
                logout.nameIdentifier.content, logout.request.sessionIndexes
            )
            sessions = list(sessions)
            if sessions:
                logout.setSessionFromDump(sessions[0].lasso_session_dump)
            else:
                get_logger().info('No session open for nameid %s', logout.nameIdentifier.content)
                return self.slo_idp_finish(logout, soap)
            user = sessions[0] and sessions[0].get_user()
            if user and user.lasso_dump:
                logout.setIdentityFromDump(user.lasso_dump)
        except lasso.Error as error:
            self.log_profile_error(logout, error, 'logout.setSessionFromDump')
            return self.slo_idp_finish(logout, soap)

        # Request is good (no problem of signature) kill all sessions
        try:
            self.kill_sessions(logout.nameIdentifier, logout.request.sessionIndexes)
        except Exception as error:
            get_session_manager().expire_session()
            get_logger().info('kill_session: %s' % error)
            return self.slo_idp_finish(logout, soap)

        # if session is still not good, clean it
        try:
            assertion = logout.session.getAssertions(logout.remoteProviderId)[0]
            if logout.request.sessionIndex and (
                assertion.authnStatement[0].sessionIndex not in logout.request.sessionIndexes
            ):
                logout.session.removeAssertion(logout.remoteProviderId)
        except Exception:
            pass

        try:
            logout.validateRequest()
        except lasso.Error as error:
            self.log_profile_error(logout, error, 'logout.validateRequest')
        return self.slo_idp_finish(logout, soap)

    def slo_idp_finish(self, logout, soap):
        try:
            logout.buildResponseMsg()
        except lasso.Error as error:
            self.log_profile_error(logout, error, 'logout.buildResponseMsg')
        else:
            if soap or (logout.msgBody and not logout.msgUrl):
                get_response().set_content_type('text/xml', 'utf-8')
                return logout.msgBody
            if logout.msgBody and logout.msgUrl:
                return self.postForm(logout)
            return redirect(logout.msgUrl)

    def metadata(self):
        try:
            with open(misc.get_abs_path(get_cfg('sp')['saml2_metadata'])) as fd:
                metadata = force_str(fd.read(), 'utf-8')
        except KeyError:
            raise errors.TraversalError()
        response = get_response()
        response.set_content_type('text/xml', 'utf-8')
        return metadata

    def public_key(self):
        response = get_response()
        response.set_content_type('application/octet-stream')
        with open(misc.get_abs_path(get_cfg('sp')['publickey'])) as fd:
            return fd.read()

    def error(self):
        request = get_request()
        if request.get_method() == 'POST':
            return self.perform_login(relay_state=request.form.get('RelayState'))
        get_response().set_title(_('Authentication error'))
        return QommonTemplateResponse(templates=['qommon/saml-error.html'], context={})

    # retain compatibility with old metadatas
    singleSignOnArtifact = assertionConsumerArtifact
    singleSignOnPost = assertionConsumerPost
    singleSignOnRedirect = assertionConsumerRedirect
