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

import copy
import ctypes
import inspect
import logging
import random
import time
import urllib.parse
import uuid
from functools import wraps
from importlib import import_module
from itertools import count

import phonenumbers
from django import forms
from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth import authenticate as dj_authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.core.mail import EmailMessage, send_mail
from django.forms.utils import ErrorList, to_current_timezone
from django.http import HttpResponse, HttpResponseRedirect
from django.http.request import QueryDict
from django.shortcuts import render, resolve_url
from django.template.context import make_context
from django.template.loader import TemplateDoesNotExist, render_to_string, select_template
from django.urls import reverse
from django.utils import html, timezone
from django.utils.encoding import iri_to_uri, uri_to_iri
from django.utils.formats import localize
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from authentic2.saml.saml2utils import filter_attribute_private_key, filter_element_private_key
from authentic2.validators import EmailValidator

from .. import app_settings, constants, crypto, plugins
from .cache import GlobalCache, cache_decorator


class CleanLogMessage(logging.Filter):
    def filter(self, record):
        record.msg = filter_attribute_private_key(record.msg)
        record.msg = filter_element_private_key(record.msg)
        return True


class MWT:
    """Memoize With Timeout"""

    _caches = {}
    _timeouts = {}

    def __init__(self, timeout=2):
        self.timeout = timeout

    def collect(self):
        """Clear cache of results which have timed out"""
        for func, cache in self._caches.items():
            updated_cache = {}
            for key in cache:
                if (time.time() - cache[key][1]) < self._timeouts[func]:
                    updated_cache[key] = cache[key]
            cache = updated_cache

    def __call__(self, f):
        self.cache = self._caches[f] = {}
        self._timeouts[f] = self.timeout

        def func(*args, **kwargs):
            kw = kwargs.items()
            kw.sort()
            key = (args, tuple(kw))
            try:
                v = self.cache[key]
                if (time.time() - v[1]) > self.timeout:
                    raise KeyError
            except KeyError:
                v = self.cache[key] = f(*args, **kwargs), time.time()
            return v[0]

        func.func_name = f.func_name

        return func


def import_from(module, name):
    module = __import__(module, fromlist=[name])
    return getattr(module, name)


def get_session_store():
    return import_module(settings.SESSION_ENGINE).SessionStore


def flush_django_session(django_session_key):
    get_session_store()(session_key=django_session_key).flush()


class IterableFactory:
    """Return an new iterable using a generator function each time this object
    is iterated."""

    def __init__(self, f):
        self.f = f

    def __iter__(self):
        return iter(self.f())


def accumulate_from_backends(request, method_name, **kwargs):
    list = []
    for backend in get_backends():
        method = getattr(backend, method_name, None)
        if callable(method):
            list += method(request, **kwargs)
    # now try plugins
    for plugin in plugins.get_plugins():
        if hasattr(plugin, method_name):
            method = getattr(plugin, method_name)
            if callable(method):
                list += method(request, **kwargs)

    return list


def load_backend(path):
    '''Load an IdP backend by its module path'''
    i = path.rfind('.')
    module, attr = path[:i], path[i + 1 :]
    try:
        mod = import_module(module)
    except ImportError as e:
        raise ImproperlyConfigured('Error importing idp backend %s: "%s"' % (module, e))
    except ValueError:
        raise ImproperlyConfigured(
            'Error importing idp backends. Is IDP_BACKENDS a correctly defined list or tuple?'
        )
    try:
        cls = getattr(mod, attr)
    except AttributeError:
        raise ImproperlyConfigured('Module "%s" does not define a "%s" idp backend' % (module, attr))
    return cls()


def get_backends():
    '''Return the list of enabled cleaned backends.'''
    backends = []

    for backend_path in app_settings.IDP_BACKENDS:
        kwargs = {}
        if not isinstance(backend_path, str):
            backend_path, kwargs = backend_path
        backend = load_backend(backend_path)
        backend.__dict__.update(kwargs)
        backends.append(backend)

    return backends


@GlobalCache(timeout=60)
def get_password_authenticator():
    from authentic2.apps.authenticators.models import LoginPasswordAuthenticator

    return LoginPasswordAuthenticator.objects.get_or_create(
        slug='password-authenticator',
        defaults={'enabled': True},
    )[0]


@cache_decorator(timeout=60)
def get_authenticators():
    from authentic2.apps.authenticators.models import BaseAuthenticator

    backends = list(
        BaseAuthenticator.authenticators.filter(enabled=True).exclude(slug='password-authenticator')
    )
    password_backend = get_password_authenticator()
    if password_backend.enabled:
        backends.append(password_backend)

    backends.sort(key=lambda backend: backend.order)
    return backends


def get_authenticator_method(authenticator, method, parameters):
    if not hasattr(authenticator, method):
        return None
    content = response = getattr(authenticator, method)(**parameters)
    if not response:
        return None
    status_code = 200
    extra_css_class = ''
    # Some authenticator methods return an HttpResponse, others return a string
    if isinstance(response, HttpResponse):
        # Force a TemplateResponse to be rendered.
        if not getattr(response, 'is_rendered', True) and callable(getattr(response, 'render', None)):
            response = response.render()
        content = response.content.decode('utf-8')
        status_code = response.status_code
        if hasattr(response, 'context_data') and response.context_data:
            extra_css_class = response.context_data.get('block-extra-css-class', '')
    return {
        'id': authenticator.get_identifier(),
        'name': authenticator._meta.verbose_name if hasattr(authenticator, '_meta') else authenticator.name,
        'content': content,
        'response': response,
        'status_code': status_code,
        'authenticator': authenticator,
        'extra_css_class': extra_css_class,
    }


def add_arg(url, key, value=None):
    '''Add a parameter to an URL'''
    key = urllib.parse.quote(key)
    if value is not None:
        add = '%s=%s' % (key, urllib.parse.quote(value))
    else:
        add = key
    if '?' in url:
        return '%s&%s' % (url, add)
    else:
        return '%s?%s' % (url, add)


def get_username(user):
    '''Retrieve the username from a user model'''
    if hasattr(user, 'USERNAME_FIELD'):
        return getattr(user, user.USERNAME_FIELD)
    else:
        return user.username


class Service:
    url = None
    name = None
    actions = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def field_names(list_of_field_name_and_titles):
    for t in list_of_field_name_and_titles:
        if isinstance(t, str):
            yield t
        else:
            yield t[0]


def is_valid_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ('http', 'https', ''):
            return True
    except Exception:
        return False


def make_url(
    to,
    args=(),
    *,
    kwargs=None,
    keep_params=False,
    params=None,
    append=None,
    request=None,
    include=None,
    exclude=None,
    fragment=None,
    absolute=False,
    resolve=True,
    next_url=None,
    sign_next_url=False,
):
    """Build an URL from a relative or absolute path, a model instance, a view
    name or view function.

    If you pass a request you can ask to keep params from it, exclude some
    of them or include only a subset of them.
    You can set parameters or append to existing one.
    If a parameter value is None, it clears the parameter from the URL, if
    the parameter was appended, it's just ignored.
    """
    if resolve:
        kwargs = kwargs or {}
        url = resolve_url(to, *args, **kwargs)
    else:
        url = to
    url = iri_to_uri(url)
    scheme, netloc, path, query_string, o_fragment = urllib.parse.urlsplit(url)
    url = uri_to_iri(urllib.parse.urlunsplit((scheme, netloc, path, '', '')))
    fragment = fragment or o_fragment
    url_params = QueryDict(query_string=query_string, mutable=True)
    if keep_params:
        assert request is not None, 'missing request'
        for key, value in request.GET.items():
            if exclude and key in exclude:
                continue
            if include and key not in include:
                continue
            url_params.setlist(key, request.GET.getlist(key))
    if params:
        for key, value in params.items():
            if value is None:
                url_params.pop(key, None)
            elif isinstance(value, (tuple, list)):
                url_params.setlist(key, value)
            else:
                url_params[key] = value
    if next_url:
        url_params[REDIRECT_FIELD_NAME] = next_url
        if sign_next_url:
            url_params[constants.NEXT_URL_SIGNATURE] = crypto.hmac_url(settings.SECRET_KEY, next_url)
    if append:
        for key, value in append.items():
            if value is None:
                continue
            if isinstance(value, (tuple, list)):
                url_params.extend({key: value})
            else:
                url_params.appendlist(key, value)
    if url_params:
        url += '?%s' % url_params.urlencode(safe='/')
    if fragment:
        url += '#%s' % fragment
    if absolute:
        if request:
            url = request.build_absolute_uri(url)
        elif hasattr(settings, 'SITE_BASE_URL'):
            url = urllib.parse.urljoin(settings.SITE_BASE_URL, url)
        else:
            raise TypeError('make_url() absolute cannot be used without request')
    # keep using unicode
    return url


# improvement over django.shortcuts.redirect
def redirect(
    request,
    to,
    args=(),
    *,
    kwargs=None,
    keep_params=False,
    params=None,
    append=None,
    include=None,
    exclude=None,
    permanent=False,
    fragment=None,
    status=302,
    resolve=True,
    next_url=None,
    sign_next_url=False,
):
    """Build a redirect response to an absolute or relative URL, eventually
    adding params from the request or new, see make_url().
    """
    url = make_url(
        to,
        args=args,
        kwargs=kwargs,
        keep_params=keep_params,
        params=params,
        append=append,
        request=request,
        include=include,
        exclude=exclude,
        fragment=fragment,
        resolve=resolve,
        next_url=next_url,
        sign_next_url=sign_next_url,
    )
    if permanent:
        status = 301
    return HttpResponseRedirect(url, status=status)


def redirect_to_login(
    request,
    login_url='auth_login',
    keep_params=True,
    include=(REDIRECT_FIELD_NAME, constants.NONCE_FIELD_NAME),
    **kwargs,
):
    '''Redirect to the login, eventually adding a nonce'''
    return redirect(request, login_url, keep_params=keep_params, include=include, **kwargs)


def continue_to_next_url(
    request, keep_params=True, include=(constants.NONCE_FIELD_NAME,), next_url=None, **kwargs
):
    next_url = next_url or select_next_url(request, settings.LOGIN_REDIRECT_URL, include_post=True)
    return redirect(request, to=next_url, keep_params=keep_params, include=include, **kwargs)


def get_nonce(request):
    nonce = request.GET.get(constants.NONCE_FIELD_NAME)
    if request.method == 'POST':
        nonce = request.POST.get(constants.NONCE_FIELD_NAME, nonce)
    return nonce


def record_authentication_event(request, how, nonce=None):
    """Record an authentication event in the session and in the database, in
    later version the database persistence can be removed"""
    from .. import models

    logging.getLogger(__name__).info('logged in (%s)', how)
    authentication_events = request.session.setdefault(constants.AUTHENTICATION_EVENTS_SESSION_KEY, [])
    # As we update a persistent object and not a session key we must
    # explicitly state that the session has been modified
    request.session.modified = True
    event = {
        'who': str(request.user),
        'who_id': getattr(request.user, 'pk', None),
        'how': how,
        'when': int(time.time()),
    }
    kwargs = {
        'who': str(request.user)[:80],
        'how': how,
    }
    nonce = nonce or get_nonce(request)
    if nonce:
        kwargs['nonce'] = nonce
        event['nonce'] = nonce
    authentication_events.append(event)

    models.AuthenticationEvent.objects.create(**kwargs)


def find_authentication_event(request, nonce):
    """Find an authentication event occurring during this session and matching
    this nonce."""
    for event in get_authentication_events(request=request):
        if event.get('nonce') == nonce:
            return event
    return None


def last_authentication_event(request=None, session=None):
    authentication_events = get_authentication_events(request=request, session=session)
    if authentication_events:
        return authentication_events[-1]
    return None


def login(request, user, how, nonce=None, record=True, next_url=None, **kwargs):
    """Login a user model, record the authentication event and redirect to next
    URL or settings.LOGIN_REDIRECT_URL."""
    from . import hooks
    from .service import get_service
    from .views import check_cookie_works

    check_cookie_works(request)
    last_login = user.last_login
    auth_login(request, user)
    if hasattr(user, 'init_to_session'):
        user.init_to_session(request.session)
    if constants.LAST_LOGIN_SESSION_KEY not in request.session:
        request.session[constants.LAST_LOGIN_SESSION_KEY] = localize(to_current_timezone(last_login), True)
    record_authentication_event(request, how, nonce=nonce)
    hooks.call_hooks('event', name='login', user=user, how=how, service=get_service(request))
    # prevent logint-hint to influence next use of the login page
    if 'login-hint' in request.session:
        del request.session['login-hint']
    if record:
        request.journal.record('user.login', how=how)
    return continue_to_next_url(request, next_url=next_url, **kwargs)


def login_require(request, next_url=None, login_url='auth_login', login_hint=(), token=None, **kwargs):
    '''Require a login and come back to current URL'''

    next_url = next_url or request.get_full_path()
    params = kwargs.setdefault('params', {})
    params[REDIRECT_FIELD_NAME] = next_url
    if login_hint:
        request.session['login-hint'] = list(login_hint)
    elif 'login-hint' in request.session:
        # clear previous login-hint if present
        del request.session['login-hint']
    if token:
        params['token'] = crypto.dumps(token)
    return redirect(request, login_url, **kwargs)


def redirect_to_logout(request, next_url=None, logout_url='auth_logout', **kwargs):
    '''Redirect to the logout and come back to the current page.'''
    next_url = next_url or request.get_full_path()
    params = kwargs.setdefault('params', {})
    params[REDIRECT_FIELD_NAME] = next_url
    return redirect(request, logout_url, **kwargs)


def redirect_and_come_back(request, to, **kwargs):
    '''Redirect to a view adding current URL as next URL parameter'''
    next_url = request.get_full_path()
    params = kwargs.setdefault('params', {})
    params[REDIRECT_FIELD_NAME] = next_url
    return redirect(request, to, **kwargs)


def generate_password():
    """Generate a password based on a certain composition based on number of
    characters based on classes of characters.
    """
    composition = ((2, '23456789'), (6, 'ABCDEFGHJKLMNPQRSTUVWXYZ'), (1, '%$/\\#@!'))
    parts = []
    for cnt, alphabet in composition:
        for dummy in range(cnt):
            parts.append(random.SystemRandom().choice(alphabet))
    random.SystemRandom().shuffle(parts)
    return ''.join(parts)


def form_add_error(form, msg, safe=False):
    # without this line form._errors is not initialized
    form.errors  # pylint: disable=pointless-statement
    errors = form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, ErrorList())
    if safe:
        msg = html.mark_safe(msg)
    errors.append(msg)


def import_module_or_class(path):
    try:
        return import_module(path)
    except ImportError:
        try:
            module, attr = path.rsplit('.', 1)
            source = import_module(module)
            return getattr(source, attr)
        except (ImportError, AttributeError):
            raise ImproperlyConfigured('unable to import class/module path: %r' % path)


def check_referer(request, skip_post=True):
    """Check that the current referer match current origin.

    Post requests are usually ignored as they are already check by the
    CSRF middleware.
    """
    if skip_post and request.method == 'POST':
        return True
    referer = request.headers.get('Referer')
    return referer and same_origin(request.build_absolute_uri(), referer)


def check_session_key(session_key):
    '''Check that a session exists for a given session_key.'''
    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
    s = SessionStore(session_key=session_key)
    # If session is empty, it's new
    return s._session != {}


def get_user_from_session_key(session_key):
    '''Get the user logged in an active session'''
    from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY, load_backend
    from django.contrib.auth.models import AnonymousUser

    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
    session = SessionStore(session_key=session_key)
    try:
        user_id = session[SESSION_KEY]
        backend_path = session[BACKEND_SESSION_KEY]
        assert backend_path in settings.AUTHENTICATION_BACKENDS
        backend = load_backend(backend_path)
        if 'session' in inspect.signature(backend.get_user).parameters:
            user = backend.get_user(user_id, session) or AnonymousUser()
        else:
            user = backend.get_user(user_id) or AnonymousUser()
    except (KeyError, AssertionError):
        user = AnonymousUser()
    return user


def to_list(func):
    @wraps(func)
    def f(*args, **kwargs):
        return list(func(*args, **kwargs))

    return f


def to_iter(func):
    @wraps(func)
    def f(*args, **kwargs):
        return IterableFactory(lambda: func(*args, **kwargs))

    return f


def normalize_attribute_values(values):
    '''Take a list of values or a single one and normalize it'''
    values_set = set()
    if isinstance(values, str) or not hasattr(values, '__iter__'):
        values = [values]
    for value in values:
        if isinstance(value, bool):
            value = str(value).lower()
        values_set.add(str(value))
    return values_set


def attribute_values_to_identifier(values):
    '''Try to find an identifier from attribute values'''
    normalized = normalize_attribute_values(values)
    assert len(normalized) == 1, 'multi-valued attribute cannot be used as an identifier'
    return list(normalized)[0]


def get_hex_uuid():
    return uuid.uuid4().hex


def get_fields_and_labels(*args):
    """Analyze fields settings and extracts ordered list of fields and
    their overriden labels.
    """
    labels = {}
    fields = []
    for arg in args:
        for field in arg:
            if isinstance(field, (list, tuple)):
                field, label = field
                labels[field] = label
            if field not in fields:
                fields.append(field)
    return fields, labels


def render_plain_text_template_to_string(template_names, ctx, request=None):
    template = select_template(template_names)
    return template.template.render(make_context(ctx, request=request, autoescape=False))


class SendEmailError(Exception):
    pass


def send_templated_mail(
    user_or_email,
    template_names,
    context=None,
    with_html=True,
    from_email=None,
    request=None,
    legacy_subject_templates=None,
    legacy_body_templates=None,
    legacy_html_body_templates=None,
    per_ou_templates=False,
    **kwargs,
):
    """Send mail to an user by using templates:
    - <template_name>_subject.txt for the subject
    - <template_name>_body.txt for the plain text body
    - <template_name>_body.html for the HTML body
    """
    from .. import middleware

    if isinstance(template_names, str):
        template_names = [template_names]
    if per_ou_templates and getattr(user_or_email, 'ou', None):
        new_template_names = []
        for template in template_names:
            new_template_names.append('_'.join((template, user_or_email.ou.slug)))
            new_template_names.append(template)
        template_names = new_template_names
    if hasattr(user_or_email, 'email'):
        email = user_or_email.email
        user = user_or_email
    else:
        email = user_or_email
        user = None

    # check email is syntaxically valid before trying to send it
    try:
        EmailValidator()(email)
    except ValidationError as e:
        logger = logging.getLogger(__name__)
        extra = {}
        if user:
            extra['user'] = user
        logger.warning(
            'send_templated_email: user=%s email=%r templates=%s error=%s', user, email, template_names, e
        )
        return

    if not request:
        request = middleware.StoreRequestMiddleware.get_request()

    ctx = copy.copy(app_settings.TEMPLATE_VARS)
    if context:
        ctx.update(context)

    subject_template_names = [template_name + '_subject.txt' for template_name in template_names]
    subject_template_names += legacy_subject_templates or []
    subject = render_plain_text_template_to_string(subject_template_names, ctx, request=request).strip()

    body_template_names = [template_name + '_body.txt' for template_name in template_names]
    body_template_names += legacy_body_templates or []
    body = render_plain_text_template_to_string(body_template_names, ctx, request=request)

    html_body = None
    html_body_template_names = [template_name + '_body.html' for template_name in template_names]
    html_body_template_names += legacy_html_body_templates or []
    if with_html and app_settings.A2_EMAIL_FORMAT != 'text/plain':
        try:
            html_body = render_to_string(html_body_template_names, ctx, request=request)
        except TemplateDoesNotExist:
            html_body = None

    if app_settings.A2_EMAIL_FORMAT == 'text/html':
        msg = EmailMessage(subject, html_body, from_email or settings.DEFAULT_FROM_EMAIL, [email], **kwargs)
        msg.content_subtype = 'html'
        msg.send()
    elif app_settings.A2_EMAIL_FORMAT == 'text/plain':
        msg = EmailMessage(subject, body, from_email or settings.DEFAULT_FROM_EMAIL, [email], **kwargs)
        msg.send()
    else:
        send_mail(
            subject,
            body,
            from_email or settings.DEFAULT_FROM_EMAIL,
            [email],
            html_message=html_body,
            **kwargs,
        )


def get_fk_model(model, fieldname):
    try:
        field = model._meta.get_field('ou')
    except FieldDoesNotExist:
        return None
    else:
        if not field.is_relation or not field.many_to_one:
            return None
        return field.related_model


def get_registration_url(request):
    next_url = select_next_url(request, settings.LOGIN_REDIRECT_URL)
    next_url = make_url(
        next_url, request=request, keep_params=True, include=(constants.NONCE_FIELD_NAME,), resolve=False
    )
    params = {REDIRECT_FIELD_NAME: next_url}
    return make_url('registration_register', params=params)


def get_token_login_url(user):
    from authentic2.models import Token

    token = Token.create('login', {'user': user.pk})
    return make_url('token_login', kwargs={'token': token.uuid_b64url}, absolute=True)


def build_activation_url(request, email, next_url=None, ou=None, **kwargs):
    from authentic2.models import Token

    data = kwargs.copy()
    data['email'] = email
    if ou:
        data['ou'] = ou.pk
    data[REDIRECT_FIELD_NAME] = next_url
    lifetime = settings.ACCOUNT_ACTIVATION_DAYS * 3600 * 24
    # invalidate any token associated with this address
    Token.objects.filter(kind='registration', content__email__iexact=email).delete()
    token = Token.create('registration', data, duration=lifetime)
    activate_url = request.build_absolute_uri(
        reverse('registration_activate', kwargs={'registration_token': token.uuid_b64url})
    )
    return activate_url


def build_deletion_url(request, **kwargs):
    data = kwargs.copy()
    data['user_pk'] = request.user.pk
    deletion_token = crypto.dumps(data)
    delete_url = request.build_absolute_uri(
        reverse('validate_deletion', kwargs={'deletion_token': deletion_token})
    )
    return delete_url


def send_registration_mail(request, email, ou, template_names=None, next_url=None, context=None, **kwargs):
    """Send a registration mail to an user. All given kwargs will be used
    to completed the user model.

    Can raise an smtplib.SMTPException
    """
    logger = logging.getLogger(__name__)
    User = get_user_model()

    if not template_names:
        template_names = ['authentic2/activation_email']

    # registration_url
    registration_url = build_activation_url(request, email=email, next_url=next_url, ou=ou, **kwargs)

    # existing accounts
    existing_accounts = User.objects.filter(email__iexact=email)
    if not app_settings.A2_EMAIL_IS_UNIQUE:
        existing_accounts = existing_accounts.filter(ou=ou)

    first_existing_account = existing_accounts.first()
    if first_existing_account:
        # if there's an existing account we send the email to its address, this is for
        # cases like CVE-2019-19844 where "A suitably crafted email address (that is
        # equal to an existing user's email address after case transformation of
        # Unicode characters) would allow an attacker to be sent a password reset
        # token for the matched user account."
        email = first_existing_account.email

    # ctx for rendering the templates
    context = context or {}
    context.update(
        {
            'registration_url': registration_url,
            'expiration_days': settings.ACCOUNT_ACTIVATION_DAYS,
            'email': email,
            'site': request.get_host(),
            'existing_accounts': existing_accounts,
        }
    )

    send_templated_mail(
        email,
        template_names,
        request=request,
        context=context,
        # legacy templates, for new templates use
        # authentic2/activation_email_body.txt
        # authentic2/activation_email_body.html
        # authentic2/activation_email_subject.txt
        legacy_subject_templates=['registration/activation_email_subject.txt'],
        legacy_body_templates=['registration/activation_email.txt'],
        legacy_html_body_templates=['registration/activation_email.html'],
    )
    logger.info('registration mail sent to %s with registration URL %s...', email, registration_url)
    request.journal.record('user.registration.request', email=email)


def send_account_deletion_code(request, user, **kwargs):
    """Send an account deletion notification code to a user.

    Can raise an smtplib.SMTPException
    """
    logger = logging.getLogger(__name__)
    deletion_url = build_deletion_url(request, **kwargs)
    context = {
        'full_name': request.user.get_full_name(),
        'user': request.user,
        'site': request.get_host(),
        'deletion_url': deletion_url,
    }
    template_names = ['authentic2/account_deletion_code']
    send_templated_mail(user, template_names, context, request=request, per_ou_templates=True)
    logger.info('account deletion code sent to %s', user.email)


def send_account_deletion_mail(request, user):
    """Send an account deletion notification mail to a user.

    Can raise an smtplib.SMTPException
    """
    logger = logging.getLogger(__name__)
    context = {'full_name': user.get_full_name(), 'user': user, 'site': request.get_host()}
    template_names = ['authentic2/account_delete_notification']
    send_templated_mail(user, template_names, context, request=request, per_ou_templates=True)
    logger.info('account deletion mail sent to %s', user.email)


def build_reset_password_url(user, request=None, next_url=None, set_random_password=True):
    '''Build a reset password URL'''
    from authentic2.models import Token

    if set_random_password:
        user.set_password(uuid.uuid4().hex)
        user.save()
    lifetime = settings.PASSWORD_RESET_TIMEOUT
    # invalidate any token associated with this user
    Token.objects.filter(kind='pw-reset', content__user=user.pk, content__email=user.email).delete()
    token = Token.create(
        'pw-reset', {'user': user.pk, 'email': user.email, REDIRECT_FIELD_NAME: next_url}, duration=lifetime
    )
    reset_url = make_url(
        'password_reset_confirm',
        kwargs={'token': token.uuid_b64url},
        request=request,
        absolute=True,
    )
    return reset_url, token


def send_password_reset_mail(
    user,
    *,
    template_names=None,
    request=None,
    token_generator=None,
    from_email=None,
    next_url=None,
    context=None,
    legacy_subject_templates=None,
    legacy_body_templates=None,
    set_random_password=True,
    sign_next_url=True,
    **kwargs,
):
    from .. import middleware

    legacy_subject_templates = legacy_subject_templates or ['registration/password_reset_subject.txt']
    legacy_body_templates = legacy_body_templates or ['registration/password_reset_email.html']

    if not user.email:
        raise ValueError('user must have an email')
    logger = logging.getLogger(__name__)
    if not template_names:
        template_names = 'authentic2/password_reset_email'
    if not request:
        request = middleware.StoreRequestMiddleware.get_request()

    ctx = {}
    ctx.update(context or {})
    ctx.update(
        {
            'user': user,
            'email': user.email,
            'expiration_days': settings.PASSWORD_RESET_TIMEOUT // 86400,
            'site': request.get_host() if request else '',
        }
    )

    # Build reset URL
    ctx['reset_url'], token = build_reset_password_url(
        user,
        request=request,
        next_url=next_url,
        set_random_password=set_random_password,
    )

    send_templated_mail(
        user,
        template_names,
        ctx,
        request=request,
        legacy_subject_templates=legacy_subject_templates,
        legacy_body_templates=legacy_body_templates,
        per_ou_templates=True,
        **kwargs,
    )
    logger.info(
        'password reset request for user %s, email sent to %s with token %s', user, user.email, token.uuid
    )


def batch_queryset(qs, size=1000, progress_callback=None):
    """Batch prefetched potentially very large queryset, it's a middle ground
    between using .iterator() which cannot be prefetched and prefetching a full
    table, which can take a larte place in memory.
    """
    for i in count(0):
        chunk = qs[i * size : (i + 1) * size]
        if not chunk:
            break
        if progress_callback:
            progress_callback(i * size)
        yield from chunk


def lower_keys(d):
    '''Convert all keys in dictionary d to lowercase'''
    return {key.lower(): value for key, value in d.items()}


def to_dict_of_set(d):
    '''Convert a dictionary of sequence into a dictionary of sets'''
    return {k: set(v) for k, v in d.items()}


def datetime_to_utc(dt):
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.astimezone(timezone.utc)


def datetime_to_xs_datetime(dt):
    return datetime_to_utc(dt).isoformat().split('.')[0] + 'Z'


@GlobalCache(timeout=10)
def get_good_origins():
    from authentic2.a2_rbac.models import OrganizationalUnit as OU
    from authentic2.models import Service

    urls = set()
    urls.update(app_settings.A2_REDIRECT_WHITELIST)
    if app_settings.A2_REGISTRATION_REDIRECT:
        origin = app_settings.A2_REGISTRATION_REDIRECT
        if isinstance(origin, (tuple, list)):
            origin = origin[0]
        urls.add(origin)
    urls.update(url for url in OU.objects.values_list('home_url', flat=True) if url)
    urls.update(Service.all_base_urls())
    return list(urls)


def good_next_url(request, next_url):
    '''Check if an URL is a good next_url'''
    if not next_url:
        return False
    if not is_ascii(next_url):
        return False
    if not next_url.isprintable():
        return False
    if not is_valid_url(next_url):
        return False
    if next_url.startswith('/\\') or next_url.startswith('\\\\'):
        return False
    if next_url.startswith('/') and (len(next_url) == 1 or next_url[1] != '/'):
        return True
    if same_origin(request.build_absolute_uri(), next_url):
        return True
    signature = request.POST.get(constants.NEXT_URL_SIGNATURE) or request.GET.get(
        constants.NEXT_URL_SIGNATURE
    )

    if signature:
        return crypto.check_hmac_url(settings.SECRET_KEY, next_url, signature)

    for origin in get_good_origins():
        if same_origin(next_url, origin):
            return True
    return False


def is_ascii(something):
    try:
        something.encode('ascii')
        return True
    except UnicodeEncodeError:
        return False


def get_next_url(params, field_name=None):
    '''Extract and decode a next_url field'''
    field_name = field_name or REDIRECT_FIELD_NAME
    next_url = params.get(field_name)
    if next_url and is_ascii(next_url) and next_url.isprintable() and is_valid_url(next_url):
        return next_url
    return None


EMPTY = object()


def select_next_url(request, default=EMPTY, field_name=None, include_post=False, replace=None):
    '''Select the first valid next URL'''
    # pylint: disable=consider-using-ternary
    if default is EMPTY:
        if request.user.is_authenticated and request.user.ou and request.user.ou.home_url:
            default = request.user.ou.home_url
        else:
            default = settings.LOGIN_REDIRECT_URL
    next_url = (include_post and get_next_url(request.POST, field_name=field_name)) or get_next_url(
        request.GET, field_name=field_name
    )
    if good_next_url(request, next_url):
        if replace:
            for key, value in replace.items():
                next_url = next_url.replace(key, urllib.parse.quote(value))
        return next_url
    return default


def human_duration(seconds):
    day = 24 * 3600
    hour = 3600
    minute = 60
    days, seconds = seconds // day, seconds % day
    hours, seconds = seconds // hour, seconds % hour
    minutes, seconds = seconds // minute, seconds % minute

    s = []
    if days:
        s.append(ngettext('%s day', '%s days', days) % days)
    if hours:
        s.append(ngettext('%s hour', '%s hours', hours) % hours)
    if minutes:
        s.append(ngettext('%s minute', '%s minutes', minutes) % minutes)
    if seconds:
        s.append(ngettext('%s second', '%s seconds', seconds) % seconds)
    return ', '.join(s)


class ServiceAccessDenied(Exception):
    def __init__(self, service):
        self.service = service


def unauthorized_view(request, service):
    context = {'callback_url': service.unauthorized_url or reverse('auth_homepage')}
    request.journal.record('user.service.sso.denial', service=service)
    return render(request, 'authentic2/unauthorized.html', context=context)


PROTOCOLS_TO_PORT = {
    'http': '80',
    'https': '443',
}


def netloc_to_host_port(netloc):
    if not netloc:
        return None, None
    splitted = netloc.split(':', 1)
    if len(splitted) > 1:
        return splitted[0], splitted[1]
    return splitted[0], None


def same_domain(domain1, domain2):
    if domain1 == domain2:
        return True

    if not domain1 or not domain2:
        return False

    if domain2.startswith('.'):
        # p1 is a sub-domain or the base domain
        if domain1.endswith(domain2) or domain1 == domain2[1:]:
            return True
    return False


def same_origin(url1, url2):
    """Checks if both URL use the same domain. It understands domain patterns on url2, i.e. .example.com
    matches www.example.com.

    If not scheme is given in url2, scheme compare is skipped.
    If not scheme and not port are given, port compare is skipped.
    The last two rules allow authorizing complete domains easily.
    """
    p1, p2 = urllib.parse.urlparse(url1), urllib.parse.urlparse(url2)
    p1_host, p1_port = netloc_to_host_port(p1.netloc)
    p2_host, p2_port = netloc_to_host_port(p2.netloc)

    if p2.scheme and p1.scheme != p2.scheme:
        return False

    if not same_domain(p1_host, p2_host):
        return False

    try:
        if (p2_port or (p1_port and p2.scheme)) and (
            (p1_port or PROTOCOLS_TO_PORT[p1.scheme]) != (p2_port or PROTOCOLS_TO_PORT[p2.scheme])
        ):
            return False
    except (ValueError, KeyError):
        return False

    return True


def simulate_authentication(request, user, method, backend=None, record=False, next_url=None, **kwargs):
    """Simulate a normal login by eventually forcing a backend attribute on the
    user instance"""
    if not getattr(user, 'backend', None) and not backend:
        backend = 'authentic2.backends.models_backend.ModelBackend'
    if backend:
        user = copy.deepcopy(user)
        user.backend = backend
    return login(request, user, method, record=record, next_url=next_url, **kwargs)


def get_manager_login_url():
    from authentic2.manager import app_settings

    return app_settings.LOGIN_URL or settings.LOGIN_URL


def send_email_change_email(user, email, request=None, next_url=None, context=None, template_names=None):
    '''Send an email to verify that user can take email as its new email'''
    assert user
    assert email

    logger = logging.getLogger(__name__)

    if template_names is None:
        template_names = ['authentic2/change_email_notification']
        legacy_subject_templates = ['profiles/email_change_subject.txt']
        legacy_body_templates = ['profiles/email_change_body.txt']
    else:
        legacy_subject_templates = None
        legacy_body_templates = None

    # build verify email URL containing a signed token
    token_content = {
        'email': email,
        'user_pk': user.pk,
    }
    if next_url is not None:
        token_content.update({'next_url': next_url})
    token = crypto.dumps(token_content)
    link = '{}?token={}'.format(reverse('email-change-verify'), token)
    link = request.build_absolute_uri(link)

    # check if email should be unique and is not
    email_is_not_unique = False
    qs = get_user_model().objects.all()
    if app_settings.A2_EMAIL_IS_UNIQUE:
        email_is_not_unique = qs.filter(email=email).exclude(pk=user.pk).exists()
    elif user.ou and user.ou.email_is_unique:
        email_is_not_unique = qs.filter(email=email, ou=user.ou).exclude(pk=user.pk).exists()
    ctx = context or {}
    ctx.update(
        {
            'email': email,
            'old_email': user.email,
            'user': user,
            'link': link,
            'site': request.get_host(),
            'domain': request.get_host(),
            'token_lifetime': human_duration(app_settings.A2_EMAIL_CHANGE_TOKEN_LIFETIME),
            'password_reset_url': request.build_absolute_uri(reverse('password_reset')),
            'email_is_not_unique': email_is_not_unique,
        }
    )
    logger.info('sent email verify email to %s for %s', email, user)
    send_templated_mail(
        email,
        template_names,
        context=ctx,
        legacy_subject_templates=legacy_subject_templates,
        legacy_body_templates=legacy_body_templates,
    )


def get_user_flag(user, name, default=None):
    '''Get a boolean flag settable at user, by a hook, globally or ou wide'''
    from .. import hooks

    user_value = getattr(user, name, None)
    if user_value is not None:
        return user_value

    hook_value = hooks.call_hooks_first_result('user_' + name, user=user)
    if hook_value is not None:
        return bool(hook_value)

    if user.ou and hasattr(user.ou, 'user_' + name):
        ou_value = getattr(user.ou, 'user_' + name, None)
        if ou_value is not None:
            return ou_value

    setting_value = getattr(app_settings, 'A2_USER_' + name.upper(), None)
    if setting_value is not None:
        return bool(setting_value)

    return default


def user_can_change_password(user=None, request=None):
    from .. import hooks

    if not app_settings.A2_REGISTRATION_CAN_CHANGE_PASSWORD:
        return False
    if request is not None and user is None and hasattr(request, 'user'):
        user = request.user
    if user is not None and hasattr(user, 'can_change_password') and user.can_change_password() is False:
        return False
    for can in hooks.call_hooks('user_can_change_password', user=user, request=request):
        if can is False:
            return can
    return True


def get_authentication_events(request=None, session=None):
    if request is not None and session is None:
        session = getattr(request, 'session', None)
    if session is not None:
        return session.get(constants.AUTHENTICATION_EVENTS_SESSION_KEY, [])
    return []


def gettid():
    """Returns OS thread id - Specific to Linux"""
    libc = ctypes.cdll.LoadLibrary('libc.so.6')
    SYS_gettid = 186
    return libc.syscall(SYS_gettid)


def authenticate(request=None, **kwargs):
    # Compatibility layer with Django 1.8
    return dj_authenticate(request=request, **kwargs)


def get_remember_cookie(request, name, count=5):
    value = request.COOKIES.get(name)
    if not value:
        return []
    try:
        parsed = value.split()
    except Exception:
        return []

    values = []
    for dummy, v in zip(range(count), parsed):
        try:
            values.append(int(v))
        except ValueError:
            return []
    return values


def prepend_remember_cookie(request, response, name, value, count=5):
    values = get_remember_cookie(request, name, count=count)
    values = [value] + values[: count - 1]
    response.set_cookie(
        name,
        ' '.join(str(value) for value in values),
        max_age=86400 * 365,  # keep preferences for 1 year
        path=request.path,
        httponly=True,
        samesite='Lax',
    )


class PasswordChangeError(Exception):
    def __init__(self, message):
        self.message = message


def is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


def parse_phone_number(phonenumber):
    parsed_pn = None
    try:
        parsed_pn = phonenumbers.parse(phonenumber)
    except phonenumbers.NumberParseException:
        try:
            parsed_pn = phonenumbers.parse(
                phonenumber,
                settings.PHONE_COUNTRY_CODES[settings.DEFAULT_COUNTRY_CODE]['region'],
            )
        except phonenumbers.NumberParseException:
            pass
    return parsed_pn


RUNTIME_SETTINGS = {
    'sso:generic_service_colour': {
        'name': _('Generic service colour'),
        'value': '',
        'type': 'colour',
    },
    'sso:generic_service_logo_url': {
        'name': _('Generic service logo URL'),
        'value': '',
        'type': 'url',
    },
    'sso:generic_service_name': {
        'name': _('Generic service name'),
        'value': '',
        'type': None,
    },
    'sso:generic_service_home_url': {
        'name': _('Generic service home URL'),
        'value': '',
        'type': 'url',
    },
    'users:backoffice_sidebar_template': {
        'name': _('Backoffice sidebar templated information'),
        'value': '',
        'type': 'text',
    },
    'users:can_change_email_address': {
        'name': _('Allow user to change their email address'),
        'value': True,
        'type': 'bool',
    },
}


def user_can_delete_phone_identifier(user):
    authenticator = get_password_authenticator()
    if not authenticator.phone_identifier_field:
        return True
    if (
        authenticator.phone_identifier_field.required
        or not authenticator.phone_identifier_field.user_editable
        or not authenticator.phone_identifier_field.user_visible
        or not authenticator.accept_email_authentication
    ):
        return False
    return True
