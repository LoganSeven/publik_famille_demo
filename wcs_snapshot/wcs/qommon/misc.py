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

import base64
import calendar
import datetime
import decimal
import hashlib
import html
import io
import json
import math
import os
import re
import string
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import phonenumbers
import requests
import unidecode
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.template import TemplateSyntaxError, VariableDoesNotExist
from django.utils.encoding import force_bytes, force_str
from django.utils.formats import number_format
from django.utils.functional import keep_lazy_text
from django.utils.html import MLStripper as DjangoMLStripper
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from django.utils.timezone import is_aware, make_naive
from PIL import Image
from quixote import get_publisher, get_request, get_response, redirect
from quixote.errors import RequestError
from quixote.html import htmlescape, htmltext
from requests.adapters import HTTPAdapter

from . import _, ezt, force_str, get_cfg, get_logger
from .errors import ConnectionError
from .template import Template

try:
    subprocess.check_call(['which', 'pdftoppm'], stdout=subprocess.DEVNULL)
    HAS_PDFTOPPM = True
except subprocess.CalledProcessError:
    HAS_PDFTOPPM = False

try:
    from schwifty import IBAN
except ImportError:
    IBAN = None

EXIF_ORIENTATION = 0x0112


# double allowed size, default Pillow limit is  around a quarter
# gigabyte for a 24 bit (3 bpp) image
#   MAX_IMAGE_PIXELS = int(1024 * 1024 * 1024 // 4 // 3)
Image.MAX_IMAGE_PIXELS = int(2 * 1024 * 1024 * 1024 // 4 // 3)


class ThumbnailError(Exception):
    pass


def get_abs_path(s):
    if not s:
        return s
    if s[0] == '/':
        return s
    return os.path.join(get_publisher().app_dir, s)


def get_lasso_server():
    if not get_cfg('sp'):
        return None
    import lasso

    server = lasso.Server(
        get_abs_path(get_cfg('sp')['saml2_metadata']), get_abs_path(get_cfg('sp')['privatekey']), None, None
    )
    server.signatureMethod = lasso.SIGNATURE_METHOD_RSA_SHA256

    # Set encryption private key
    encryption_privatekey = get_abs_path(get_cfg('sp').get('encryption_privatekey'))
    if encryption_privatekey and os.path.exists(encryption_privatekey):
        try:
            server.setEncryptionPrivateKey(encryption_privatekey)
        except lasso.Error:
            get_logger().warning('Failed to set encryption private key')

    for klp, idp in sorted(get_cfg('idp', {}).items(), key=lambda k: k[0]):
        try:
            server.addProvider(
                lasso.PROVIDER_ROLE_IDP,
                get_abs_path(idp['metadata']),
                get_abs_path(idp.get('publickey')),
                get_abs_path(idp.get('cacertchain')),
            )
        except lasso.Error as error:
            if error[0] == lasso.SERVER_ERROR_ADD_PROVIDER_PROTOCOL_MISMATCH:
                continue
            if error[0] == lasso.SERVER_ERROR_ADD_PROVIDER_FAILED:
                continue
            raise

        encryption_mode = lasso.ENCRYPTION_MODE_NONE
        if idp.get('encrypt_nameid', False):
            encryption_mode |= lasso.ENCRYPTION_MODE_NAMEID
        provider_t = get_provider(klp)
        provider = server.getProvider(provider_t.providerId)
        if provider is not None:
            provider.setEncryptionMode(encryption_mode)

    return server


def get_provider_label(provider):
    if not provider:
        return None
    if not hasattr(provider, 'getOrganization'):
        return provider.providerId

    organization = provider.getOrganization()
    if not organization:
        return provider.providerId

    name = re.findall('<OrganizationDisplayName.*>(.*?)</OrganizationDisplayName>', organization)
    if not name:
        name = re.findall('<OrganizationName.*>(.*?)</OrganizationName>', organization)
        if not name:
            return provider.providerId
    return htmltext(name[0])


def get_provider(provider_key):
    lp = get_cfg('idp', {}).get(provider_key)
    if not lp:
        raise KeyError()

    import lasso

    publickey_fn = None
    if lp.get('publickey'):
        publickey_fn = get_abs_path(lp['publickey'])
    # cacertchain (not really necessary to get provider label)

    try:
        provider = lasso.Provider(lasso.PROVIDER_ROLE_IDP, get_abs_path(lp['metadata']), publickey_fn, None)
    except lasso.Error:
        raise KeyError()

    return provider


def get_provider_key(provider_id):
    return provider_id.replace('://', '-').replace('/', '-').replace('?', '-').replace(':', '-')


def simplify(s, space='-', force_letter_first=False):
    if s is None:
        return ''
    if not isinstance(s, str):
        s = force_str('%s' % s, 'utf-8', errors='ignore')
    s = unidecode.unidecode(s)
    s = re.sub(r'[^\w\s\'\-%s]' % space, '', s).strip().lower()
    s = re.sub(r'[\s\'\-_%s]+' % space, space, s).strip(space)
    if force_letter_first and s[0] not in string.ascii_letters:
        return 'n' + s
    return s


def strftime(fmt, dt):
    if not dt:
        return ''
    if not isinstance(dt, datetime.datetime):
        if isinstance(dt, datetime.date):
            dt = datetime.datetime(dt.year, dt.month, dt.day)
        else:
            # consider it a 9 elements tuple
            dt = datetime.datetime(*dt[:6])
    return dt.strftime(fmt)


def localstrftime(t):
    if not t:
        return ''
    if isinstance(t, datetime.datetime) and is_aware(t):
        t = make_naive(t)
    return strftime(datetime_format(), t)


DATE_FORMATS = {
    'C': ['%Y-%m-%d', '%y-%m-%d'],
    'fr': ['%d/%m/%Y', '%d/%m/%y'],
}

DATETIME_FORMATS = {
    'C': [
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
        '%y-%m-%d %H:%M',
        '%y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S%z',
        '%y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S.%f%z',
    ],
    'fr': [
        '%d/%m/%Y %H:%M',
        '%d/%m/%Y %H:%M:%S',
        '%d/%m/%Y %Hh%M',
        '%d/%m/%y %H:%M',
        '%d/%m/%y %H:%M:%S',
        '%d/%m/%y %Hh%M',
    ],
}


def datetime_format():
    lang = get_publisher().current_language
    if lang not in DATETIME_FORMATS:
        lang = 'C'
    return DATETIME_FORMATS[lang][0]


def date_format():
    lang = get_publisher().current_language
    if lang not in DATE_FORMATS:
        lang = 'C'
    return DATE_FORMATS[lang][0]


BAD_ISO_DATE_REGEXP = re.compile(r'^0\d\d\d-\d\d-\d\d$')


def get_as_datetime(s, strict_datetime=False):
    if not s:
        raise ValueError

    s = force_str(s)
    if s and BAD_ISO_DATE_REGEXP.match(s):
        # iso date with year starting with 0, it's likely currently being
        # typed in an HTML5 date input widget, consider it invalid.
        raise ValueError('invalid date, leading 0')
    # prefer current locale
    formats = [datetime_format()]
    if not strict_datetime:
        formats.append(date_format())
    for value in DATETIME_FORMATS.values():
        formats.extend(value)
    if not strict_datetime:
        for value in DATE_FORMATS.values():
            formats.extend(value)
    exception = ValueError()
    for format_string in formats:
        try:
            return datetime.datetime.strptime(s, format_string)
        except ValueError as e:
            exception = e
    raise exception


def site_encode(s):
    if s is None:
        return None
    return force_str(s)


def ellipsize(s, length=30, truncate='(…)'):
    s = force_str(s)
    if s and len(s) > length:
        if length > 3:
            s = Truncator(s).chars(length, truncate=truncate)
        else:
            s = s[:length]
    return force_str(s)


def get_month_name(month):
    month_names = [
        _('January'),
        _('February'),
        _('March'),
        _('April'),
        _('May'),
        _('June'),
        _('July'),
        _('August'),
        _('September'),
        _('October'),
        _('November'),
        _('December'),
    ]
    return month_names[month - 1]


def format_time(datetime, formatstring, gmtime=False):
    if not datetime:
        return '?'
    if type(datetime) in (int, float):
        if gmtime:
            datetime = time.gmtime(datetime)
        else:
            datetime = time.localtime(datetime)
    if len(datetime) == 2:
        year, month = datetime
        weekday = None
    elif len(datetime) == 3:
        year, month, day = datetime
        weekday = None
    else:
        year, month, day, hour, minute, second, weekday = datetime[:7]

    weekday_names = [
        _('Monday'),
        _('Tuesday'),
        _('Wednesday'),
        _('Thursday'),
        _('Friday'),
        _('Saturday'),
        _('Sunday'),
    ]

    if weekday is not None:
        weekday_name = weekday_names[weekday]
        lower_weekday_name = weekday_name.lower()
        abbr_weekday_name = weekday_name[:3]

    month_name = get_month_name(month)
    lower_month_name = month_name.lower()
    abbr_month_name = month_name[:3]

    return formatstring % locals()


def _http_request(
    url,
    method='GET',
    body=None,
    headers=None,
    cert_file=None,
    timeout=None,
    raise_on_http_errors=False,
    error_url=None,
):
    error_url = error_url or url
    headers = headers or {}
    headers['Publik-Caller-URL'] = ''
    form = get_publisher().substitutions.get_context_variables(mode='lazy').get('form')
    if form:
        if hasattr(form, '_formdata') and form._formdata.id:
            headers['Publik-Caller-URL'] = form._formdata.get_backoffice_url()
        elif hasattr(form, '_formdef'):
            headers['Publik-Caller-URL'] = form._formdef.get_admin_url()
    pub = get_publisher()
    pub.reload_cfg()

    splitted_url = urllib.parse.urlsplit(url)
    if not splitted_url.scheme and not splitted_url.netloc:
        raise ConnectionError(str(_('invalid URL "%s", maybe using missing variables')) % error_url)
    if splitted_url.scheme not in ('http', 'https'):
        raise ConnectionError(str(_('invalid scheme in URL "%s"' % error_url)))

    hostname = splitted_url.netloc
    timeout = timeout or settings.REQUESTS_TIMEOUT

    new_hostname = pub.get_site_option(hostname, 'legacy-urls')
    if new_hostname:
        hostname = new_hostname
        url = splitted_url._replace(netloc=hostname).geturl()

    if cert_file is None:
        for url_prefix, cert in settings.REQUESTS_CERT.items():
            if url.startswith(url_prefix):
                cert_file = cert
                break

    # re-use HTTP adapter to get connection pooling and keep-alive.
    adapter = getattr(get_publisher(), '_http_adapter', None)
    if adapter is None:
        adapter = get_publisher()._http_adapter = HTTPAdapter()

    session = requests.Session()
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    try:
        get_publisher().log_http_request(method, url)
        response = session.request(
            method,
            url,
            headers=headers,
            data=body,
            timeout=timeout,
            cert=cert_file,
            proxies=settings.REQUESTS_PROXIES,
        )
    except requests.Timeout:
        raise ConnectionError('connection timed out while fetching the page')
    except requests.RequestException as err:
        raise ConnectionError('error in HTTP request to %s (%s)' % (hostname, err))

    data = response.content
    status = response.status_code
    auth_header = response.headers.get('WWW-Authenticate')

    if raise_on_http_errors and not (200 <= status < 300):
        raise ConnectionError('error in HTTP request to %s (status: %s)' % (error_url, status))

    return response, status, data, auth_header


def urlopen(url, data=None, error_url=None):
    data = _http_request(
        url, 'GET' if data is None else 'POST', body=data, raise_on_http_errors=True, error_url=error_url
    )[2]
    return io.BytesIO(data)


def http_get_page(url, **kwargs):
    return _http_request(url, **kwargs)


def http_patch_request(url, body=None, **kwargs):
    return _http_request(url, 'PATCH', body, **kwargs)


def http_post_request(url, body=None, **kwargs):
    return _http_request(url, 'POST', body, **kwargs)


def http_delete_request(url, **kwargs):
    return _http_request(url, 'DELETE', **kwargs)


def unlazy(x):
    return x.get_value() if hasattr(x, 'get_value') else x


@contextmanager
def no_complex(context):
    allow_complex = context.get('allow_complex')
    context['allow_complex'] = False
    try:
        yield context
    finally:
        context['allow_complex'] = allow_complex


def get_variadic_url(url, variables=None, encode_query=True):
    if not Template.is_template_string(url):
        return url

    if variables is None:
        variables = get_publisher().substitutions.get_context_variables(mode='lazy')

    # django template
    if (
        '{{' in url
        or '{%' in url
        or (get_publisher() and get_publisher().has_site_option('disable-ezt-support'))
    ):
        try:
            with no_complex(variables):
                url = Template(url, autoescape=False).render(variables)
            p = urllib.parse.urlsplit(url)
            scheme, netloc, path, query, fragment = (p.scheme, p.netloc, p.path, p.query, p.fragment)
            if path.startswith('//'):
                # don't let double slash happen at the root of the URL, this
                # happens when a template such as {{url}}/path is used (with
                # {{url}} already ending with a slash).
                path = path[1:]
            return urllib.parse.urlunsplit((scheme, netloc, path, query, fragment))
        except (TemplateSyntaxError, VariableDoesNotExist):
            return url

    # ezt template, try to be safe
    def ezt_substitute(template, variables):
        tmpl = ezt.Template()
        template = template.replace('\ue001', '[').replace('\ue002', ']')
        tmpl.parse(template)
        fd = io.StringIO()
        tmpl.generate(fd, variables)
        return fd.getvalue()

    def partial_quote(string):
        # unquote brackets, as there may be further processing that needs them
        # intact.
        return urllib.parse.quote(string).replace('%5B', '\ue001').replace('%5D', '\ue002')

    # replace bracket characters as python 3.11 would confuse them with IPv6 addresses
    # and abort with "'xxx' does not appear to be an IPv4 or IPv6 address".
    url = url.replace('[', '\ue001').replace(']', '\ue002')

    p = urllib.parse.urlsplit(url)
    scheme, netloc, path, query, fragment = p.scheme, p.netloc, p.path, p.query, p.fragment
    if netloc and '\ue001' in netloc:
        netloc = ezt_substitute(netloc, variables)
    if path and '\ue001' in path:
        if scheme == '' and netloc == '':
            # this happened because the variable was set in the scheme
            # (ex: http[https]://www.example.net) or because the value starts
            # with a variable name (ex: [url]); in that situation we do not
            # quote at all.
            if path.count('//') == 1:
                # there were no / in the original path (the two / comes from
                # the scheme/netloc separation, this means there is no path)
                before_path = ezt_substitute(path, variables)
                p2 = urllib.parse.urlsplit(before_path)
                scheme, netloc, path = p2.scheme, p2.netloc, p2.path
            else:
                # there is a path, we need to get back to the original URL and
                # split it on the last /, to isolate the path part.
                lastslash = '/' if path.endswith('/') else ''
                if '/' in path:
                    before_path, path = path.rsplit('/', 1)
                else:
                    before_path, path = path, ''
                before_path = ezt_substitute(before_path, variables)
                p2 = urllib.parse.urlsplit(before_path)
                scheme, netloc = p2.scheme, p2.netloc
                if p2.path:
                    if not path:
                        path, query2 = p2.path + lastslash, p2.query
                    else:
                        path, query2 = p2.path + '/' + path, p2.query
                    if query and query2:
                        query += '&' + query2
                    else:
                        query = query or query2
        if path:
            path = partial_quote(ezt_substitute(path, variables))
        if not path:
            path = '/'
        if path.startswith('//'):
            path = path[1:]
    if fragment and '\ue001' in fragment:
        fragment = partial_quote(ezt_substitute(fragment, variables))
    if query and '\ue001' in query:
        p_qs = urllib.parse.parse_qsl(query)
        if len(p_qs) == 0:
            # this happened because the query string has no key/values,
            # probably because it's a single substitution variable (ex:
            # http://www.example.net/foobar?[query])
            query = ezt_substitute(query, variables)
        else:
            query = []
            for k, v in p_qs:
                if '\ue001' in k:
                    k = ezt_substitute(k, variables)
                if '\ue001' in v:
                    v = ezt_substitute(v, variables)
                query.append((k, v))
            if encode_query:
                query = urllib.parse.urlencode(query)
            else:
                query = '&'.join('%s=%s' % (k, v) for (k, v) in query)
    return (
        urllib.parse.urlunsplit((scheme, netloc, path, query, fragment))
        .replace('\ue001', '[')
        .replace('\ue002', ']')
    )


def get_foreground_colour(background_colour):
    """Calculates the luminance of the given colour (six hexadecimal digits)
    and returns an appropriate foreground colour."""
    # luminance coefficients taken from section C-9 from
    # http://www.faqs.org/faqs/graphics/colorspace-faq/
    background_colour = background_colour.removeprefix('#')
    brightess = (
        int(background_colour[0:2], 16) * 0.212671
        + int(background_colour[2:4], 16) * 0.715160
        + int(background_colour[4:6], 16) * 0.072169
    )
    if brightess > 128:
        fg_colour = 'black'
    else:
        fg_colour = 'white'
    return fg_colour


def xml_node_text(node):
    if node is None or node.text is None:
        return None
    return force_str(node.text)


class JSONEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        self.allow_files = kwargs.pop('allow_files', True)
        super().__init__(*args, **kwargs)

    def default(self, o):
        # unlazy
        o = o.get_value() if hasattr(o, 'get_value') else o

        if isinstance(o, datetime.datetime):
            return o.isoformat()

        if isinstance(o, datetime.date):
            return o.strftime('%Y-%m-%d')

        if isinstance(o, datetime.time):
            return o.strftime('%H:%M:%S')

        if isinstance(o, decimal.Decimal):
            return number_format(o, use_l10n=False)

        if isinstance(o, bytes):
            return o.decode()

        if isinstance(o, set):
            return list(o)

        if not self.allow_files and (is_upload(o) or hasattr(o, 'base_filename')):
            raise TypeError('files are not allowed')

        if hasattr(o, 'get_json_value'):
            return o.get_json_value()

        if hasattr(o, 'base_filename'):
            return {
                'filename': o.base_filename,
                'content_type': o.content_type or 'application/octet-stream',
                'content': base64.b64encode(o.get_content()),
                'content_is_base64': True,
            }

        if o.__class__.__name__ == '__proxy__':
            # lazy gettext
            return str(o)

        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, o)


def json_response(data):
    get_response().set_content_type('application/json')
    if get_request().get_environ('HTTP_ORIGIN'):
        get_response().set_header('Access-Control-Allow-Origin', get_request().get_environ('HTTP_ORIGIN'))
        get_response().set_header('Access-Control-Allow-Credentials', 'true')
        get_response().set_header('Access-Control-Allow-Headers', 'x-requested-with')
    json_str = json.dumps(data, cls=JSONEncoder)
    for variable in ('jsonpCallback', 'callback'):
        if variable in get_request().form:
            get_response().set_content_type('application/javascript')
            json_str = '%s(%s);' % (get_request().form[variable], json_str)
            break
    return json_str


def parse_isotime(s):
    s = s.replace('+00:00Z', 'Z')  # clean lemonldap dates with both timezone and Z
    t = time.strptime(s, '%Y-%m-%dT%H:%M:%SZ')
    return calendar.timegm(t)


def file_digest(content, chunk_size=100000):
    digest = hashlib.sha256()
    content.seek(0)

    def read_chunk():
        return content.read(chunk_size)

    for chunk in iter(read_chunk, b''):
        digest.update(chunk)
    return digest.hexdigest()


def is_svg_filetype(filetype):
    return filetype and filetype.split('+')[0] == 'image/svg'


def can_thumbnail(content_type):
    if content_type == 'application/pdf':
        return bool(HAS_PDFTOPPM and Image)
    if content_type and content_type.startswith('image/'):
        return bool(Image is not None)
    return False


def get_thumbnail(filepath, content_type=None, size=None):
    if not filepath or not can_thumbnail(content_type or ''):
        raise ThumbnailError()

    # check if thumbnail already exists
    thumbs_dir = os.path.join(get_publisher().app_dir, 'thumbs')
    try:
        os.mkdir(thumbs_dir)
    except FileExistsError:
        pass
    thumb_filepath = force_bytes(filepath)
    if size:
        thumb_filepath = '%s-%s-%s' % (thumb_filepath, *size)
    thumb_filepath = os.path.join(thumbs_dir, hashlib.sha256(force_bytes(thumb_filepath)).hexdigest())
    if os.path.exists(thumb_filepath):
        with open(thumb_filepath, 'rb') as f:
            return f.read()

    size = size or (500, 300)

    # generate thumbnail
    if content_type == 'application/pdf':
        try:
            fp = io.BytesIO(
                subprocess.check_output(
                    ['pdftoppm', '-png', '-scale-to-x', '500', '-scale-to-y', '-1', filepath]
                )
            )
        except subprocess.CalledProcessError:
            raise ThumbnailError()
    else:
        fp = open(filepath, 'rb')  # pylint: disable=consider-using-with
    try:
        try:
            kwargs = {'formats': ['JPEG', 'PNG', 'GIF']}
            if Image.UnidentifiedImageError is ZeroDivisionError:
                kwargs = {}
            image = Image.open(fp, **kwargs)
        except (Image.DecompressionBombError, Image.UnidentifiedImageError):
            raise ThumbnailError()
        try:
            exif = image._getexif()
        except Exception:
            exif = None

        if exif:
            # orientation code from sorl.thumbnail (engines/pil_engine.py)
            orientation = exif.get(EXIF_ORIENTATION)

            if orientation == 2:
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 3:
                image = image.rotate(180)
            elif orientation == 4:
                image = image.transpose(Image.FLIP_TOP_BOTTOM)
            elif orientation == 5:
                image = image.rotate(-90, expand=1).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 6:
                image = image.rotate(-90, expand=1)
            elif orientation == 7:
                image = image.rotate(90, expand=1).transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 8:
                image = image.rotate(90, expand=1)

        try:
            image.thumbnail(size)
        except (ValueError, SyntaxError):
            # PIL can raise syntax error on broken PNG files
            # * File "PIL/PngImagePlugin.py", line 119, in read
            # * raise SyntaxError("broken PNG file (chunk %s)" % repr(cid))
            # PIL can raise ValueError "tile cannot extend outside image"
            # on broken JPEG files.
            raise OSError
        image_thumb_fp = io.BytesIO()
        image.convert('RGBA').save(image_thumb_fp, 'PNG')
    except OSError:
        # failed to create thumbnail.
        raise ThumbnailError()
    finally:
        fp.close()

    # store thumbnail
    with open(thumb_filepath, 'wb') as f:
        f.write(image_thumb_fp.getvalue())

    return image_thumb_fp.getvalue()


def normalize_geolocation(lat_lon):
    '''Fit lat into -90/90 and lon into -180/180'''

    def wrap(x, mini, maxi):
        diff = maxi - mini
        return ((x - mini) % diff + diff) % diff + mini

    try:
        lat = decimal.Decimal(lat_lon['lat'])
        lon = decimal.Decimal(lat_lon['lon'])
        lat = float(wrap(lat, decimal.Decimal('-90.0'), decimal.Decimal('90.0')))
        lon = float(wrap(lon, decimal.Decimal('-180.0'), decimal.Decimal('180.0')))
    except decimal.InvalidOperation:
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        # avoid infinitity and NaN
        return None
    return {'lat': lat, 'lon': lon}


def html2text(text):
    return site_encode(html.unescape(strip_tags(force_str(text))))


def validate_luhn(string_value, length=None):
    '''Verify Luhn checksum on a string representing a number'''
    if not string_value:
        return False
    if length is not None and len(string_value) != length:
        return False
    if not is_ascii_digit(string_value):
        return False

    # take all digits counting from the right, double value for digits pair
    # index (counting from 1), if double has 2 digits take their sum
    checksum = 0
    for i, x in enumerate(reversed(string_value)):
        if i % 2 == 0:
            checksum += int(x)
        else:
            checksum += sum(int(y) for y in str(2 * int(x)))
    if checksum % 10 != 0:
        return False
    return True


def is_ascii_digit(string_value):
    return string_value and all(x in '0123456789' for x in string_value)


def get_valid_phone_number(string_value, region_codes=None, country_codes=None):
    # get string_value as a valid phonenumber in default or specified region_codes,
    # with additional check against country_codes if given.
    #
    # region_codes are strings like BE for Belgium, RE for La Reunion, etc.
    # country_codes are numeric codes like 32 for Belgium, 262 for La Reunion, etc.

    if not re.match(r'^[0\+][\(\)\d\.\s]+$', string_value or ''):
        # leading zero or +, then digits, dots, or spaces
        return None

    region_codes = region_codes or [get_publisher().get_phone_local_region_code()]
    for region_code in region_codes:
        pn = None
        try:
            pn = phonenumbers.parse(string_value, region_code)
        except phonenumbers.NumberParseException:
            continue
        if not phonenumbers.is_valid_number(pn):
            continue
        if country_codes and pn.country_code not in country_codes:
            continue
        return pn
    return None


def get_french_country_and_region_codes():
    country_codes = [
        # France has 6 country codes in E.164 system :
        33,  # Metropolitan France (FR)
        262,  # Réunion (RE)
        508,  # Saint-Pierre-et-Miquelon (PM)
        590,  # Guadeloupe, Saint-Barthélemy, Saint-Martin (GP)
        594,  # Guyanne (GF)
        596,  # Martinique (MQ)
    ]
    region_codes = [phonenumbers.region_code_for_country_code(x) for x in country_codes]
    local_region_code = get_publisher().get_phone_local_region_code()
    if local_region_code in region_codes:
        # set parsing preference to configured local region code
        region_codes = [local_region_code] + [x for x in region_codes if x != local_region_code]
    return country_codes, region_codes


def validate_phone_fr(string_value):
    country_codes, region_codes = get_french_country_and_region_codes()
    pn = get_valid_phone_number(string_value, region_codes=region_codes, country_codes=country_codes)
    if pn:
        # extra check as libphonenumbers will allow combining international
        # and local prefixes (ex: +33 01 23 45 67 89)
        allowed = [f'{pn.country_code}{pn.national_number}', f'0{pn.national_number}']
        cleaned_number = ''.join(x for x in string_value if is_ascii_digit(x)).strip().removeprefix('00')
        return cleaned_number in allowed
    return bool(pn)


def validate_mobile_phone_local(string_value, region_code=None):
    region_code = region_code or get_publisher().get_phone_local_region_code()
    if region_code == 'FR':
        # in case of France extend list of country/region codes, so it's not just
        # metropolitan France.
        country_codes, region_codes = get_french_country_and_region_codes()
    else:
        country_codes = []
        for country_code, region_codes in phonenumbers.COUNTRY_CODE_TO_REGION_CODE.items():
            if region_code in region_codes:
                country_codes = [country_code]
                break
        region_codes = [region_code]
    pn = get_valid_phone_number(string_value, region_codes, country_codes)
    return bool(
        pn
        and phonenumbers.number_type(pn)
        in (phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE)
    )


def get_formatted_phone(number, region_code=None):
    if not region_code:
        region_code = get_publisher().get_phone_local_region_code()
    pn = get_valid_phone_number(number, [region_code])
    return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.NATIONAL) if pn else number


def normalize_phone_number_for_fts(number):
    pn = get_valid_phone_number(number)
    return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164) if pn else number


def validate_siren(string_value):
    return validate_luhn(string_value, length=9)


def validate_siret(string_value):
    # special case : La Poste
    if not is_ascii_digit(string_value):
        return False
    if (
        string_value.startswith('356000000')
        and len(string_value) == 14
        and sum(int(x) for x in string_value) % 5 == 0
    ):
        return True
    return validate_luhn(string_value, length=14)


def validate_nir(string_value):
    '''https://fr.wikipedia.org/wiki/Num%C3%A9ro_de_s%C3%A9curit%C3%A9_sociale_en_France'''
    if not string_value:
        return False
    if len(string_value) != 15:
        return False
    if string_value[0] == '0':  # sex
        return False
    if string_value[7:10] == '000':  # municipality
        return False
    if string_value[10:13] == '000':  # order
        return False
    dept = string_value[5:7]
    if dept == '2A':
        string_value = string_value.replace('2A', '19', 1)
    elif dept == '2B':
        string_value = string_value.replace('2B', '18', 1)
    if not is_ascii_digit(string_value):
        return False
    month = int(string_value[3:5])
    if month < 50 and month not in list(range(1, 13)) + [20] + list(range(30, 43)):
        return False
    nir_key = string_value[13:]
    return int(nir_key) == 97 - int(string_value[:13]) % 97


def validate_belgian_nrn(string_value):
    # https://fr.wikipedia.org/wiki/Numéro_de_registre_national
    if not string_value:
        return False
    if len(string_value) != 11:
        return False
    if not is_ascii_digit(string_value):
        return False
    _year, month, day, _index, checksum = (
        string_value[:2],
        string_value[2:4],
        string_value[4:6],
        string_value[6:9],
        string_value[9:11],
    )
    if int(month) > 12:
        return False
    if int(day) > 31:
        return False
    return (97 - int(string_value[:9]) % 97 == int(checksum)) or (
        97 - int('2' + string_value[:9]) % 97 == int(checksum)
    )


IBAN_LENGTH = {
    # from https://www.iban.com/structure
    'AD': 24,
    'AE': 23,
    'AL': 28,
    'AT': 20,
    'AZ': 28,
    'BA': 20,
    'BE': 16,
    'BG': 22,
    'BH': 22,
    'BR': 29,
    'BY': 28,
    'CH': 21,
    'CR': 22,
    'CY': 28,
    'CZ': 24,
    'DE': 22,
    'DK': 18,
    'DO': 28,
    'EE': 20,
    'EG': 29,
    'ES': 24,
    'FI': 18,
    'FO': 18,
    'FR': 27,
    'GB': 22,
    'GE': 22,
    'GI': 23,
    'GL': 18,
    'GR': 27,
    'GT': 28,
    'HR': 21,
    'HU': 28,
    'IE': 22,
    'IL': 23,
    'IQ': 23,
    'IS': 26,
    'IT': 27,
    'JO': 30,
    'KW': 30,
    'KZ': 20,
    'LB': 28,
    'LC': 32,
    'LI': 21,
    'LT': 20,
    'LU': 20,
    'LV': 21,
    'MC': 27,
    'MD': 24,
    'ME': 22,
    'MK': 19,
    'MR': 27,
    'MT': 31,
    'MU': 30,
    'NL': 18,
    'NO': 15,
    'PK': 24,
    'PL': 28,
    'PS': 29,
    'PT': 25,
    'QA': 29,
    'RO': 24,
    'RS': 22,
    'SA': 24,
    'SC': 31,
    'SE': 24,
    'SI': 19,
    'SK': 24,
    'SM': 27,
    'ST': 25,
    'SV': 28,
    'TL': 23,
    'TN': 24,
    'TR': 26,
    'UA': 29,
    'VA': 22,
    'VG': 24,
    'XK': 20,
    # FR includes:
    'GF': 27,
    'GP': 27,
    'MQ': 27,
    'RE': 27,
    'PF': 27,
    'TF': 27,
    'YT': 27,
    'NC': 27,
    'BL': 27,
    'MF': 27,
    'PM': 27,
    'WF': 27,
    # GB includes:
    'IM': 22,
    'GG': 22,
    'JE': 22,
    # FI includes:
    'AX': 18,
    # ES includes:
    'IC': 24,
    'EA': 24,
}


def validate_iban(string_value):
    '''https://fr.wikipedia.org/wiki/International_Bank_Account_Number'''
    if not string_value:
        return False
    if IBAN:
        try:
            IBAN(string_value, validate_bban=True)
        except ValueError:
            return False
        return True
    string_value = string_value.upper().strip().replace(' ', '')
    country_code = string_value[:2]
    iban_key = string_value[2:4]
    bban = string_value[4:]
    if not (country_code.isalpha() and country_code.isupper()):
        return False
    if IBAN_LENGTH.get(country_code) and len(string_value) != IBAN_LENGTH[country_code]:
        return False
    if not is_ascii_digit(iban_key):
        return False
    if not bban or is_ascii_digit(bban) and int(bban) == 0:
        # bban is empty or a list of 0
        return False
    dummy_iban = bban + country_code + '00'
    dummy_iban_converted = ''
    for car in dummy_iban:
        if 'A' <= car <= 'Z':
            dummy_iban_converted += str(ord(car) - ord('A') + 10)
        else:
            dummy_iban_converted += car
    if not is_ascii_digit(dummy_iban_converted):
        return False
    return int(iban_key) == 98 - int(dummy_iban_converted) % 97


def validate_url(string_value):
    try:
        URLValidator(schemes=['http', 'https'])(string_value)
    except ValidationError:
        return False
    return True


def get_int_or_400(value):
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        raise RequestError()


def get_order_by_or_400(value):
    if value in (None, ''):
        return None
    if not (isinstance(value, str) and re.match(r'-?[a-zA-Z0-9_-]+$', value)):
        raise RequestError()
    return value


def is_upload(obj):
    # we can't use isinstance() because obj can be a
    # wcs.qommon.form.PicklableUpload or a qommon.form.PicklableUpload
    return obj.__class__.__name__ == 'PicklableUpload'


def is_attachment(obj):
    # ditto
    return obj.__class__.__name__ == 'AttachmentEvolutionPart'


class QLookupRedirect:
    """
    Class to use to interrupt a _q_lookup method and redirect.
    """

    def __init__(self, url):
        self.url = url

    def _q_traverse(self, path):
        return redirect(self.url)


def get_document_types(current_document_type):
    document_types = {
        '_audio': {
            'label': _('Sound files'),
            'mimetypes': ['audio/*'],
        },
        '_video': {
            'label': _('Video files'),
            'mimetypes': ['video/*'],
        },
        '_image': {
            'label': _('Image files'),
            'mimetypes': ['image/*'],
        },
    }
    # Local document types
    document_types.update(get_cfg('filetypes', {}))
    for key, document_type in document_types.items():
        document_type['id'] = key
        document_type['label'] = str(document_type['label'])
    # add current file type if it does not exist anymore in the settings
    cur_dt = current_document_type
    if cur_dt and cur_dt['id'] not in document_types:
        document_types[cur_dt['id']] = cur_dt
    return document_types


def get_document_type_value_options(current_document_type):
    document_types = get_document_types(current_document_type)
    default_file_type_id = get_cfg('misc', {}).get('default_file_type')
    default_document_type_label = '---'
    if default_file_type_id:
        filetypes_cfg = get_cfg('filetypes', {})
        default_document_type = filetypes_cfg.get(default_file_type_id, {})
        default_document_type_label = _('Default value (%s)') % default_document_type.get('label')
    options = [({}, default_document_type_label, '')]
    options += [(doc_type, doc_type['label'], key) for key, doc_type in document_types.items()]
    return options


def xml_response(obj, filename, content_type='text/xml', include_id=True, **kwargs):
    etree = obj.export_to_xml(include_id=include_id, **kwargs)
    if hasattr(obj, 'get_admin_url'):
        etree.attrib['url'] = obj.get_admin_url()
    ET.indent(etree)
    response = get_response()
    response.set_content_type(content_type)
    response.set_header('content-disposition', 'attachment; filename=%s' % filename)
    return '<?xml version="1.0"?>\n' + ET.tostring(etree).decode('utf-8')


def get_type_name(value):
    from wcs.workflows import AttachmentSubstitutionProxy, NamedAttachmentsSubstitutionProxy

    from .upload_storage import PicklableUpload

    object_type_names = {
        type(None): _('no value'),
        bool: _('boolean'),
        bytes: _('bytes'),
        datetime.datetime: _('datetime'),
        datetime.date: _('date'),
        datetime.time: _('time'),
        decimal.Decimal: _('decimal number'),
        int: _('integer number'),
        list: _('list'),
        str: _('string'),
        PicklableUpload: _('file'),
        AttachmentSubstitutionProxy: _('file'),
        NamedAttachmentsSubstitutionProxy: _('file'),
    }
    object_type_name = object_type_names.get(value.__class__, value.__class__.__name__)
    return object_type_name


class MLStripper(DjangoMLStripper):
    def __init__(self, allowed_tags):
        super().__init__()
        self.reset()
        self.fed = []
        self.allowed_tags = allowed_tags

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed_tags:
            return

        if tag == 'a':
            for attr in attrs:
                if attr[0] == 'href':
                    self.fed.append('<a href="%s">' % attr[1])
                    return
        if tag == 'br':
            self.fed.append('<br />')
            return

        self.fed.append('<%s>' % tag)

    def handle_endtag(self, tag):
        if tag not in self.allowed_tags:
            return

        if tag == 'br':
            return

        self.fed.append('</%s>' % tag)


def _strip_once(value, allowed_tags):
    """
    Internal tag stripping utility used by strip_some_tags.
    """
    s = MLStripper(allowed_tags)
    s.feed(value)
    s.close()
    return s.get_data()


@keep_lazy_text
def strip_some_tags(value, allowed_tags):
    """Return the given HTML with all tags stripped except allowed_tags."""
    # Note: in typical case this loop executes _strip_once once. Loop condition
    # is redundant, but helps to reduce number of executions of _strip_once.
    value = str(value)
    while '<' in value and '>' in value:
        new_value = _strip_once(value, allowed_tags)
        if value.count('<') == new_value.count('<'):
            # _strip_once wasn't able to detect more tags.
            break
        value = new_value
    return mark_safe(value)


def get_dependencies_from_template(string):
    from wcs.carddef import CardDef
    from wcs.data_sources import NamedDataSource
    from wcs.formdef import FormDef
    from wcs.wscalls import NamedWsCall

    if not isinstance(string, str) or string.startswith('='):
        return

    for ws_slug in re.findall(r'webservice\.([\w_]+)', string):
        yield NamedWsCall.get_by_slug(ws_slug, ignore_errors=True)

    for ws_slug in re.findall(r'{% +webservice +[\'"]([\w_]+)', string):
        yield NamedWsCall.get_by_slug(ws_slug, ignore_errors=True)

    for ds_slug in re.findall(r'data_source\.([\w_]+)', string):
        yield NamedDataSource.get_by_slug(ds_slug, ignore_errors=True)

    for carddef_slug in re.findall(r'cards\|objects:"([\w_-]+)"', string):
        yield CardDef.get_by_slug(carddef_slug, ignore_errors=True)

    for formdef_slug in re.findall(r'forms\|objects:"([\w_-]+)"', string):
        yield FormDef.get_by_slug(formdef_slug, ignore_errors=True)


def parse_decimal(value, do_raise=False, keep_none=False):
    value = unlazy(value)
    if keep_none and (value is None or value == ''):
        return None
    if isinstance(value, bool):
        # treat all booleans as 0 (contrary to Python behaviour where
        # decimal(True) == 1).
        value = 0
    if isinstance(value, str):
        # replace , by . for French users comfort
        value = value.replace(',', '.')
    try:
        value = decimal.Decimal(value)
        if value == value.to_integral_value():
            return value.quantize(decimal.Decimal(1))
        return decimal.Decimal(value).quantize(decimal.Decimal('1.000000')).normalize()
    except (ArithmeticError, TypeError, decimal.InvalidOperation, ValueError):
        if do_raise:
            raise ValueError(_('invalid decimal value: %r') % value)
        return decimal.Decimal(0)


def mark_spaces(s):
    s = str(htmlescape(str(s)))
    got_sub = False

    def get_sub(match):
        nonlocal got_sub
        got_sub = True
        return ''.join(
            f'<span class="escaped-code-point" data-escaped="[U+{ord(x):04X}]"><span class="char">&nbsp;</span></span>'
            for x in match.group()
        )

    s = re.sub(r'^(\s+)', get_sub, s)
    s = re.sub(r'(\s+)$', get_sub, s)
    s = re.sub(r'(\s\s+)', get_sub, s)
    s = htmltext(s)
    if got_sub:
        s = htmltext(
            '<button class="toggle-escape-button" role="button" title="%s"></button>%s'
            % (_('This line contains invisible characters.'), s)
        )
    return s


def get_reverse_geocoding_data(lat, lon):
    from wcs.wscalls import call_webservice

    dummy, status, data = call_webservice(
        get_publisher().get_reverse_geocoding_service_url(),
        method='GET',
        qs_data={
            'format': 'json',
            'addressdetails': '1',
            'lat': str(lat),
            'lon': str(lon),
            'accept-language': get_publisher().get_site_language() or 'en',
        },
        cache=True,
        cache_duration=3600,
        timeout=5,
        notify_on_errors=False,
        record_on_errors=False,
    )
    return data if status == 200 else '{}'


class classproperty:
    def __init__(self, f):
        self.f = f

    def __get__(self, obj, owner):
        return self.f(owner)
