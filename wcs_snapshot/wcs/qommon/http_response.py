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

import hashlib

import quixote.http_response
from django.utils.encoding import force_bytes
from quixote import get_publisher, get_request


class HTTPResponse(quixote.http_response.HTTPResponse):
    javascript_scripts = None
    javascript_code_parts = None
    css_includes = None
    after_jobs = None
    raw = False  # in case of html content, send result as is (True) or embedded in page template (False)

    def __init__(self, charset=None, **kwargs):
        quixote.http_response.HTTPResponse.__init__(self, charset=charset, **kwargs)
        if not charset:
            self.charset = get_publisher().site_charset
        self.filter = {}

    def _gen_cookie_headers(self):
        return []

    def reset_includes(self):
        self.javascript_scripts = None
        self.javascript_code_parts = None
        self.css_includes = None

    def add_javascript(self, script_names):
        if not self.javascript_scripts:
            self.javascript_scripts = []
        debug_mode = get_publisher().cfg.get('debug', {}).get('debug_mode', False)
        mappings = {
            'jquery.js': '../xstatic/jquery.min.js',
            'jquery-ui.js': '../xstatic/jquery-ui.min.js',
            'select2.js': '../xstatic/select2.min.js',
        }
        if debug_mode:
            mappings['jquery.js'] = '../xstatic/jquery.js'
            mappings['jquery-ui.js'] = '../xstatic/jquery-ui.js'
            mappings['select2.js'] = '../xstatic/select2.js'
        if get_request().is_in_backoffice():
            included_js_libraries = []
        else:
            branding_cfg = get_publisher().cfg.get('branding') or {}
            included_js_libraries = branding_cfg.get('included_js_libraries') or []
        for script_name in script_names:
            mapped_script_name = mappings.get(script_name, script_name)
            if mapped_script_name not in self.javascript_scripts:
                if script_name == 'qommon.map.js':
                    self.add_javascript(['jquery.js'])
                    self.add_javascript(['../xstatic/leaflet.js'])
                    self.add_javascript(['../xstatic/leaflet-gesture-handling.min.js'])
                    self.add_css_include('../xstatic/leaflet.css')
                    self.add_css_include('../xstatic/leaflet-gesture-handling.min.css')
                    self.add_javascript(['leaflet-gps.js'])
                    if '{x}' not in get_publisher().get_site_option('map-tile-urltemplate'):
                        self.add_javascript(['../xstatic/mapbox-gl.js'])
                        self.add_css_include('../xstatic/mapbox-gl.css')
                        self.add_javascript(['../xstatic/leaflet-mapbox-gl.js'])
                    self.add_javascript(['../../i18n.js'])
                if script_name == 'qommon.wysiwyg.js':
                    self.add_javascript(
                        [
                            'jquery.js',
                            '../ckeditor/ckeditor/ckeditor.js',
                            '../ckeditor/ckeditor/adapters/jquery.js',
                        ]
                    )
                if script_name == 'qommon.fileupload.js':
                    self.add_javascript(
                        [
                            '../../i18n.js',
                            'jquery.js',
                            'jquery-ui.js',
                            'jquery.iframe-transport.js',
                            'exif.js',
                            'jquery.fileupload.js',
                        ]
                    )
                if script_name not in included_js_libraries:
                    self.javascript_scripts.append(str(mapped_script_name))
                if script_name == 'afterjob.js':
                    self.add_javascript_code(
                        'var QOMMON_ROOT_URL = "%s";\n'
                        % get_publisher().get_application_static_files_root_url()
                    )
                if script_name == 'popup.js':
                    self.add_javascript(['../../i18n.js', 'jquery.js', 'jquery-ui.js'])
                    if not get_request().is_in_backoffice():
                        self.add_javascript(['gadjo.js'])
                if script_name == 'qommon.geolocation.js':
                    self.add_javascript(['jquery.js', 'qommon.slugify.js'])
                    self.add_javascript_code(
                        'var WCS_ROOT_URL = "%s";\n' % get_publisher().get_frontoffice_url()
                    )
                    default_geocoding_country = get_publisher().get_site_option('default-geocoding-country')
                    if default_geocoding_country:
                        self.add_javascript_code(
                            'var WCS_DEFAULT_GEOCODING_COUNTRY = "%s";' % default_geocoding_country
                        )
                if script_name == 'wcs.listing.js':
                    self.add_javascript(['../../i18n.js', 'jquery.js', 'jquery-ui.js'])
                if script_name == 'qommon.admin.js':
                    self.add_javascript(['../../i18n.js', 'jquery.js', 'qommon.slugify.js'])
                if script_name == 'select2.js':
                    self.add_javascript(['jquery.js', '../../i18n.js', 'qommon.forms.js'])
                    if script_name not in included_js_libraries:
                        # assume a theme embedding select2.js will also include the css parts
                        self.add_css_include('select2.css')

    def add_javascript_code(self, code):
        if not self.javascript_code_parts:
            self.javascript_code_parts = []
        if code not in self.javascript_code_parts:
            self.javascript_code_parts.append(code)

    def get_javascript_for_header(self):
        s = ''
        if self.javascript_scripts:
            from .admin.menu import get_vc_version

            version_hash = hashlib.md5(force_bytes(get_vc_version())).hexdigest()
            root_url = get_publisher().get_root_url() + get_publisher().qommon_static_dir
            s += '\n'.join(
                [
                    '<script type="text/javascript" src="%sjs/%s?%s"></script>'
                    % (root_url, str(x), version_hash)
                    for x in self.javascript_scripts
                ]
            )
            s += '\n'
        if self.javascript_code_parts:
            s += '<script type="text/javascript">'
            s += '\n'.join(self.javascript_code_parts)
            s += '\n</script>\n'
        return s

    def add_css_include(self, css_include):
        debug_mode = get_publisher().cfg.get('debug', {}).get('debug_mode', False)
        mappings = {
            'select2.css': '../xstatic/select2.min.css',
        }
        if debug_mode:
            mappings['select2.css'] = '../xstatic/select2.css'
        css_include = mappings.get(css_include, css_include)
        if not self.css_includes:
            self.css_includes = []
        if css_include not in self.css_includes:
            self.css_includes.append(css_include)

    def get_css_includes_for_header(self):
        if not self.css_includes:
            return ''
        from .admin.menu import get_vc_version

        version_hash = hashlib.md5(force_bytes(get_vc_version())).hexdigest()
        root_url = get_publisher().get_root_url() + get_publisher().qommon_static_dir
        return '\n'.join(
            [
                '<link rel="stylesheet" type="text/css" href="%scss/%s?%s" />' % (root_url, x, version_hash)
                for x in self.css_includes
            ]
        )

    def set_robots_no_index(self):
        self.set_header('X-Robots-Tag', 'noindex')

    def set_backoffice_section(self, section):
        self.filter['backoffice_section'] = section

    def set_title(self, title, page_title=None):
        self.filter['title'] = title
        self.filter['page_title'] = page_title
