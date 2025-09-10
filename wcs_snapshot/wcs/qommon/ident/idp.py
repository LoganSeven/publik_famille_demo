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

import os
import re
import tempfile

try:
    import lasso
except ImportError:
    lasso = None

import xml.etree.ElementTree as ET

from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from .. import _, errors, get_cfg, misc, saml2utils, template, x509utils
from ..admin.cfg import cfg_submit, hobo_kwargs
from ..admin.menu import command_icon
from ..form import (
    CheckboxWidget,
    FileWidget,
    Form,
    HtmlWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetDict,
)
from ..storage import atomic_write
from .base import AuthMethod

ADMIN_TITLE = _('SAML2')


def is_idp_managing_user_attributes():
    return get_cfg('sp', {}).get('idp-manage-user-attributes', False)


def is_idp_managing_user_roles():
    return get_cfg('sp', {}).get('idp-manage-roles', False)


def get_file_content(filename):
    try:
        with open(filename) as fd:
            return fd.read()
    except Exception:
        return None


def get_text_file_preview(filename):
    """Return a preformatted HTML blocks displaying content
    of filename, or None if filename is not accessible
    """
    content = get_file_content(str(filename))
    return htmltext('<pre>%s</pre>') % content if content else None


class MethodDirectory(Directory):
    _q_exports = ['login', 'register', 'token']

    def login(self):
        idps = get_cfg('idp', {})

        if not lasso:
            raise Exception('lasso is missing, idp method cannot be used')

        if len(idps) == 0:
            return template.error_page(_('SSO support is not yet configured'))

        t = IdPAuthMethod().login()
        if t:
            return t

        form = Form(enctype='multipart/form-data')
        form.add_hidden('method', 'idp')
        options = []
        value = None
        providers = {}
        for dummy, idp in sorted(get_cfg('idp', {}).items(), key=lambda k: k[0]):
            if idp.get('hide'):
                continue
            p = lasso.Provider(
                lasso.PROVIDER_ROLE_IDP,
                misc.get_abs_path(idp['metadata']),
                misc.get_abs_path(idp.get('publickey')),
                None,
            )
            providers[p.providerId] = p

        for p in providers.values():
            label = misc.get_provider_label(p)
            options.append((p.providerId, label, p.providerId))
            if not value:
                value = p.providerId
        form.add(RadiobuttonsWidget, 'idp', value=value, options=options, delim=htmltext('<br/>'))
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            idp = form.get_widget('idp').parse()
            saml = get_publisher().root_directory_class.saml
            return saml.perform_login(idp)

        get_response().set_title(_('Login'))
        r = TemplateIO(html=True)
        r += htmltext('<p>%s</p>') % _('Select the identity provider you want to use.')
        r += form.render()
        return r.getvalue()

    def register(self):
        if not get_cfg('saml_identities', {}).get('registration-url'):
            raise errors.TraversalError()
        ctx = get_publisher().substitutions.get_context_variables(mode='lazy')
        ctx['next_url'] = get_request().get_frontoffice_url()
        registration_url = misc.get_variadic_url(get_cfg('saml_identities', {}).get('registration-url'), ctx)
        return redirect(registration_url)


class AdminIDPDir(Directory):
    title = _('Identity Providers')

    _q_exports = ['', 'new', 'new_remote']

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('idp/', self.title))
        return Directory._q_traverse(self, path)

    def _q_index(self):
        get_response().set_title(self.title)
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % _('Identity Providers')
        r += htmltext('<span class="actions">\n')
        r += htmltext(' <a rel="popup" href="new_remote">%s</a>\n') % _('Create new from remote URL')
        r += htmltext(' <a href="new">%s</a>\n') % _('New')
        r += htmltext('</span>')
        r += htmltext('</div>')

        r += htmltext('<ul class="biglist idp--list">')
        for kidp, idp in sorted(get_cfg('idp', {}).items(), key=lambda k: k[0]):
            p = None
            if idp and isinstance(idp, dict):
                p = lasso.Provider(
                    lasso.PROVIDER_ROLE_IDP,
                    misc.get_abs_path(idp.get('metadata')),
                    misc.get_abs_path(idp.get('publickey')),
                    None,
                )
                try:  # this handling since "if p is None: continue" doesn't work
                    if p.providerId == '':
                        pass
                except TypeError:
                    p = None

            r += htmltext('<li class="biglistitem">')
            r += htmltext('<span class="biglistitem--content">')
            if p:
                r += htmltext('<span class="label"><a href="%s/">%s</a></span>') % (
                    kidp,
                    misc.get_provider_label(p),
                )
            else:
                r += htmltext('<span class="label">%s %s</span>') % (kidp, _('Broken'))
            if p and p.providerId != misc.get_provider_label(p):
                r += htmltext('<span class="biglistitem--content-details">')
                r += htmltext('<span class="data">%s</span>') % p.providerId
                r += htmltext('</span>')
            r += htmltext('</span>')
            r += htmltext('<p class="commands">')
            r += command_icon('%s/edit' % kidp, 'edit')
            r += command_icon('%s/delete' % kidp, 'remove')
            r += htmltext('</p></li>')
        r += htmltext('</ul>')
        return r.getvalue()

    def _q_lookup(self, component):
        return AdminIDPUI(component)

    @classmethod
    def user_fields_options(cls):
        """List user formdef fields for the SelectWidget of the attribute
        mapping setting"""
        user_class = get_publisher().user_class
        options = []
        for field in user_class.get_formdef().fields:
            options.append((str(field.id), field.label, str(field.id)))
        return options

    @classmethod
    def get_form(cls, instance=None):
        instance = instance or {}
        form = Form(enctype='multipart/form-data')
        form.add(FileWidget, 'metadata', title=_('Metadata'), required=not instance)
        form.add(FileWidget, 'publickey', title=_('Public Key'), required=False)
        form.add(FileWidget, 'cacertchain', title=_('CA Certificate Chain'), required=False)
        form.add(
            CheckboxWidget,
            'hide',
            title=_('Hide this provider from user lists'),
            required=False,
            value=instance.get('hide'),
        )
        form.add(
            SingleSelectWidget,
            'nameidformat',
            title=_('Requested NameID format'),
            value=instance.get('nameidformat', 'persistent'),
            options=[
                ('persistent', _('Persistent')),
                ('unspecified', _('Username (like Google Apps)')),
                ('email', _('Email')),
            ],
        )

        form.add(
            WidgetDict,
            'admin-attributes',
            value=instance.get(
                'admin-attributes',
                {
                    'local-admin': 'true',
                },
            ),
            title=_('Administrator attribute matching rules'),
            element_value_type=StringWidget,
            hint=_(
                'First column match attribute names, second is for matching '
                'attribute value. If no rule is given, admin flag is never '
                'set. Flag is set if any rule match.'
            ),
        )
        options = cls.user_fields_options()
        if options:
            form.add(
                WidgetDict,
                'attribute-mapping',
                value=instance.get('attribute-mapping', {}),
                title=_('Attribute mapping'),
                element_value_type=SingleSelectWidget,
                element_value_kwargs={'options': options},
                hint=_('First column match attribute names, second row is the user field to fill'),
            )
        form.add_submit('submit', _('Submit'))
        return form

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        form = self.get_form()

        if not ('submit' in get_request().form and form.is_submitted()) or form.has_errors():
            get_response().set_title(_('New Identity Provider'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('New Identity Provider')
            r += form.render()
            return r.getvalue()

        return self.submit_new(form)

    def submit_new(self, form, key_provider_id=None):
        get_publisher().reload_cfg()
        cfg_idp = get_cfg('idp', {})
        get_publisher().cfg['idp'] = cfg_idp

        metadata, publickey, cacertchain = (None, None, None)
        if form.get_widget('metadata').parse():
            metadata = force_str(form.get_widget('metadata').parse().fp.read())
        if form.get_widget('publickey').parse():
            publickey = form.get_widget('publickey').parse().fp.read()
        if form.get_widget('cacertchain').parse():
            cacertchain = form.get_widget('cacertchain').parse().fp.read()

        if not key_provider_id:
            try:
                provider_id = re.findall(r'(provider|entity)ID="(.*?)"', metadata)[0][1]
            except IndexError:
                return template.error_page(_('Bad metadata'))
            key_provider_id = misc.get_provider_key(provider_id)

        dir = get_publisher().app_dir
        metadata_fn = 'idp-%s-metadata.xml' % key_provider_id

        if metadata:
            atomic_write(os.path.join(dir, metadata_fn), force_bytes(metadata))
        if publickey:
            publickey_fn = 'idp-%s-publickey.pem' % key_provider_id
            atomic_write(os.path.join(dir, publickey_fn), force_bytes(publickey))
        else:
            publickey_fn = None

        if cacertchain:
            cacertchain_fn = 'idp-%s-cacertchain.pem' % key_provider_id
            atomic_write(os.path.join(dir, cacertchain_fn), force_bytes(cacertchain))
        else:
            cacertchain_fn = None

        cfg_idp[key_provider_id] = {
            'metadata': metadata_fn,
            'publickey': publickey_fn,
            'cacertchain': cacertchain_fn,
        }
        for key in ('hide', 'nameidformat', 'admin-attributes', 'attribute-mapping'):
            if form.get_widget(key):
                cfg_idp[key_provider_id][key] = form.get_widget(key).parse()
        idp = cfg_idp[key_provider_id]
        p = lasso.Provider(
            lasso.PROVIDER_ROLE_IDP,
            misc.get_abs_path(idp['metadata']),
            misc.get_abs_path(idp.get('publickey')),
            None,
        )
        try:
            misc.get_provider_label(p)
        except TypeError:
            del cfg_idp[key_provider_id]
            if metadata:
                os.unlink(os.path.join(dir, metadata_fn))
            if publickey:
                os.unlink(os.path.join(dir, publickey_fn))
            if cacertchain:
                os.unlink(os.path.join(dir, cacertchain_fn))
            return template.error_page(_('Bad metadata'))

        get_publisher().write_cfg()
        return redirect('.')

    def new_remote(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'metadata_url', title=_('URL to metadata'), required=True, size=60)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            metadata_pathname = None
            metadata_url = form.get_widget('metadata_url').parse()
            try:
                rfd = misc.urlopen(metadata_url)
            except misc.ConnectionError as e:
                form.set_error('metadata_url', _('Failed to retrieve file (%s)') % e)
            except Exception:
                form.set_error('metadata_url', _('Failed to retrieve file'))
            else:
                s = rfd.read()
                metadata_pathname = tempfile.mkstemp('.metadata')[1]
                atomic_write(metadata_pathname, force_bytes(s))
                try:
                    lasso.Provider(lasso.PROVIDER_ROLE_IDP, metadata_pathname, None, None)
                except lasso.Error:
                    form.get_widget('metadata_url').set_error(_('File looks like a bad metadata file'))
                else:
                    t = self.submit_new_remote(metadata_pathname, metadata_url)
                    if t:
                        return t
                    form.get_widget('metadata_url').set_error(_('Bad metadata'))

            if metadata_pathname and os.path.exists(metadata_pathname):
                os.unlink(metadata_pathname)

        get_response().breadcrumb.append(('new_remote', _('New')))
        get_response().set_title(_('New Identity Provider'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Identity Provider')
        r += form.render()
        return r.getvalue()

    def submit_new_remote(self, metadata_pathname, metadata_url):
        role = lasso.PROVIDER_ROLE_IDP

        get_publisher().reload_cfg()
        cfg_idp = get_cfg('idp', {})
        get_publisher().cfg['idp'] = cfg_idp

        with open(metadata_pathname) as fd:
            metadata = fd.read()

        if metadata_pathname and os.path.exists(metadata_pathname):
            os.unlink(metadata_pathname)

        try:
            provider_id = re.findall(r'(provider|entity)ID="(.*?)"', metadata)[0][1]
        except IndexError:
            return None

        key_provider_id = misc.get_provider_key(provider_id)
        old_metadata_fn = None
        old_publickey_fn = None

        metadata_fn = 'idp-%s-metadata.xml' % key_provider_id
        publickey_fn = 'idp-%s-publickey.pem' % key_provider_id
        if old_metadata_fn and os.path.exists(misc.get_abs_path(old_metadata_fn)):
            os.rename(misc.get_abs_path(old_metadata_fn), misc.get_abs_path(metadata_fn))
        if old_publickey_fn and os.path.exists(old_publickey_fn):
            os.rename(misc.get_abs_path(old_publickey_fn), misc.get_abs_path(publickey_fn))

        if key_provider_id not in cfg_idp:
            cfg_idp[key_provider_id] = {}

        cfg_idp[key_provider_id]['role'] = role
        cfg_idp[key_provider_id]['metadata'] = metadata_fn

        # save URL so they can be automatically updated later
        cfg_idp[key_provider_id]['metadata_url'] = metadata_url

        atomic_write(misc.get_abs_path(metadata_fn), force_bytes(metadata))
        get_publisher().write_cfg()

        if not get_request():
            # this allows this method to be called outsite of a
            # request/response cycle.
            return key_provider_id
        return redirect('.')


class AdminIDPUI(Directory):
    _q_exports = ['', 'delete', 'edit', 'update_remote']

    def __init__(self, component):
        self.idp = get_cfg('idp')[component]
        self.idpk = component
        get_response().breadcrumb.append(('%s/' % component, _('Provider')))

    def _q_index(self):
        p = lasso.Provider(
            lasso.PROVIDER_ROLE_IDP,
            misc.get_abs_path(self.idp['metadata']),
            misc.get_abs_path(self.idp.get('publickey')),
            misc.get_abs_path(self.idp.get('cacertchain', None)),
        )
        get_response().set_title(_('Identity Provider'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s - %s</h2>') % (_('Identity Provider'), p.providerId)
        r += htmltext('<div class="form">')
        r += htmltext('<h3>%s</h3>') % _('Metadata')
        r += htmltext('<pre>')
        with open(misc.get_abs_path(self.idp['metadata'])) as fd:
            metadata = fd.read()
        try:
            metadata_tree = ET.fromstring(metadata)
            ET.indent(metadata_tree)
            metadata_text = ET.tostring(metadata_tree).decode()
        except Exception as e:
            metadata_text = str(_('Unable to display metadata (%s)') % e)
        r += metadata_text
        r += htmltext('</pre>')
        r += htmltext('</div>')

        r += htmltext('<p>')
        r += htmltext('<a class="button" href="edit">%s</a> ') % _('Edit')
        if self.idp.get('metadata_url'):
            r += htmltext('<a class="button" href="update_remote">%s</a>') % _('Update from remote URL')
        r += htmltext('</p>')
        return r.getvalue()

    def edit(self):
        form = AdminIDPDir.get_form(self.idp)

        if not ('submit' in get_request().form and form.is_submitted()) or form.has_errors():
            get_response().set_title(_('Edit Identity Provider'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Edit Identity Provider')
            r += form.render()
            return r.getvalue()

        return AdminIDPDir().submit_new(form, self.idpk)  # XXX: not ok for metadata file path

    def delete(self):
        try:
            p = lasso.Provider(
                lasso.PROVIDER_ROLE_SP,
                misc.get_abs_path(self.idp.get('metadata')),
                misc.get_abs_path(self.idp.get('publickey')),
                None,
            )
            if p.providerId is None:
                # this is an empty test to refer to p.providerId and raise an
                # exception if it is not available
                pass
        except Exception:
            p = None
        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget('<p>%s</p>' % _('You are about to irrevocably remove this identity provider.'))
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')
        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Identity Provider'))
            r = TemplateIO(html=True)
            if p:
                r += htmltext('<h2>%s %s</h2>') % (_('Deleting'), p.providerId)
            else:
                r += htmltext('<h2>%s</h2>') % _('Deleting Identity Provider')
            r += form.render()
            return r.getvalue()
        return self.delete_submitted()

    def delete_submitted(self):
        del get_publisher().cfg['idp'][self.idpk]
        dir = get_publisher().app_dir
        metadata_fn = os.path.join(dir, 'idp-%s-metadata.xml' % self.idpk)
        publickey_fn = os.path.join(dir, 'idp-%s-publickey.pem' % self.idpk)
        cacertchain_fn = os.path.join(dir, 'idp-%s-cacertchain.pem' % self.idpk)
        for f in (metadata_fn, publickey_fn, cacertchain_fn):
            if os.path.exists(f):
                os.unlink(f)
        get_publisher().write_cfg()
        return redirect('..')

    def update_remote(self):
        get_publisher().reload_cfg()
        cfg_idp = get_cfg('idp', {})
        get_publisher().cfg['idp'] = cfg_idp

        metadata_url = self.idp.get('metadata_url')
        try:
            metadata_fd = misc.urlopen(metadata_url)
        except misc.ConnectionError:
            return template.error_page('failed to download')
        metadata = force_str(metadata_fd.read())

        provider_id = re.findall(r'(provider|entity)ID="(.*?)"', metadata)[0][1]
        try:
            provider_id = re.findall(r'(provider|entity)ID="(.*?)"', metadata)[0][1]
        except IndexError:
            return template.error_page(_('Bad metadata'))

        new_key_provider_id = misc.get_provider_key(provider_id)
        key_provider_id = self.idpk
        old_publickey_fn = None
        old_cacertchain_fn = None
        if key_provider_id and new_key_provider_id != key_provider_id:
            # provider id changed, remove old files
            cfg_idp[new_key_provider_id] = cfg_idp[key_provider_id]
            old_publickey_fn = misc.get_abs_path('idp-%s-publickey.pem' % key_provider_id)
            old_cacertchain_fn = misc.get_abs_path('idp-%s-cacertchain.pem' % key_provider_id)
            del cfg_idp[key_provider_id]
            if old_publickey_fn and os.path.exists(old_publickey_fn):
                os.unlink(old_publickey_fn)
            if old_cacertchain_fn and os.path.exists(old_cacertchain_fn):
                os.unlink(old_cacertchain_fn)

        key_provider_id = new_key_provider_id
        metadata_fn = 'idp-%s-metadata.xml' % key_provider_id

        if key_provider_id not in cfg_idp:
            cfg_idp[key_provider_id] = {}

        cfg_idp[key_provider_id]['metadata'] = metadata_fn

        if metadata:
            atomic_write(misc.get_abs_path(metadata_fn), force_bytes(metadata))

        lp = cfg_idp[key_provider_id]
        publickey_fn = None
        if 'publickey' in lp and os.path.exists(misc.get_abs_path(lp['publickey'])):
            publickey_fn = misc.get_abs_path(lp['publickey'])
        try:
            lasso.Provider(lasso.PROVIDER_ROLE_IDP, misc.get_abs_path(lp['metadata']), publickey_fn)
        except lasso.Error:
            # this happens when the public key is missing from both params
            # and metadata file
            if publickey_fn:
                return (None, template.error_page(_('Bad metadata')))
            return (None, template.error_page(_('Bad metadata or missing public key')))
        try:
            misc.get_provider(key_provider_id)
        except (TypeError, KeyError):
            del cfg_idp[key_provider_id]
            if metadata_fn and os.path.exists(metadata_fn):
                os.unlink(misc.get_abs_path(metadata_fn))
            if publickey_fn and os.path.exists(publickey_fn):
                os.unlink(misc.get_abs_path(publickey_fn))
            return template.error_page(_('Bad metadata'))

        get_publisher().write_cfg()

        return redirect('../%s/' % key_provider_id)


class MethodAdminDirectory(Directory):
    title = ADMIN_TITLE
    label = _('Configure SAML identification method')

    _q_exports = ['', 'sp', 'idp', 'identities']

    idp = AdminIDPDir()

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('idp/', self.title))
        return Directory._q_traverse(self, path)

    def _q_index(self):
        get_response().set_title(self.title)
        r = TemplateIO(html=True)
        r += htmltext('<h2>SAML 2.0</h2>')
        r += htmltext('<a class="button button-paragraph" href="sp">%s <p>%s</p></a>') % (
            _('Service Provider'),
            _('Configure SAML 2.0 parameters'),
        )

        if get_cfg('sp', {}).get('saml2_providerid') and (
            hasattr(get_publisher().root_directory_class, 'saml')
        ):
            metadata_url = '%s/metadata.xml' % get_cfg('sp')['saml2_base_url']
            r += htmltext('<a class="button button-paragraph" href="%s">%s <p>%s</p></a>') % (
                metadata_url,
                _('SAML 2.0 Service Provider Metadata'),
                _('Download Service Provider SAML 2.0 Metadata file'),
            )

        r += htmltext('<a class="button button-paragraph" href="idp/">%s <p>%s</p></a>') % (
            _('Identity Providers'),
            _('Add and remove identity providers'),
        )

        r += htmltext('<a class="button button-paragraph" href="identities">%s <p>%s</p></a>') % (
            _('Identities'),
            _('Configure identities creation'),
        )
        return r.getvalue()

    def generate_rsa_keypair(self, branch='sp'):
        publickey, privatekey = x509utils.generate_rsa_keypair()
        encryptionpublickey, encryptionprivatekey = x509utils.generate_rsa_keypair()
        cfg_sp = get_cfg(branch, {})
        self.configure_sp_metadatas(cfg_sp, publickey, privatekey, encryptionpublickey, encryptionprivatekey)

    def sp(self):
        get_response().breadcrumb.append(('sp', _('Service Provider')))
        saml2_base_url = get_cfg('sp', {}).get('saml2_base_url', None)
        req = get_request()

        if not saml2_base_url:
            saml2_base_url = '%s://%s%ssaml' % (
                req.get_scheme(),
                req.get_server(),
                get_publisher().get_root_url(),
            )

        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget,
            'saml2_providerid',
            title=_('SAML 2.0 Provider ID'),
            size=50,
            required=False,
            value=get_cfg('sp', {}).get('saml2_providerid', saml2_base_url + '/metadata'),
        )
        form.add(
            StringWidget,
            'saml2_base_url',
            title=_('SAML 2.0 Base URL'),
            size=50,
            required=False,
            value=saml2_base_url,
        )

        form.add(
            StringWidget,
            'organization_name',
            title=_('Organisation Name'),
            size=50,
            value=get_cfg('sp', {}).get('organization_name', None),
        )

        dir = get_publisher().app_dir
        publickey_fn = os.path.join(dir, 'public-key.pem')
        encryption_publickey_fn = os.path.join(dir, 'encryption-public-key.pem')
        form.add(FileWidget, 'privatekey', title=_('Signing Private Key'))
        form.add(
            FileWidget,
            'publickey',
            title=_('Signing Public Key'),
            hint=get_text_file_preview(publickey_fn) or _('There is no signing key pair configured.'),
        )
        form.add(FileWidget, 'encryption_privatekey', title=_('Encryption Private Key'))
        form.add(
            FileWidget,
            'encryption_publickey',
            title=_('Encryption Public Key'),
            hint=get_text_file_preview(encryption_publickey_fn)
            or _('There is no encryption key pair configured.'),
        )

        form.add(
            CheckboxWidget,
            'authn-request-signed',
            title=_('Sign authentication request'),
            hint=_('Better to let it checked'),
            value=get_cfg('sp', {}).get('authn-request-signed', True),
        )

        form.add(
            CheckboxWidget,
            'want-assertion-signed',
            title=_('IdP must crypt assertions'),
            hint=_('Better to let it checked'),
            value=get_cfg('sp', {}).get('want-assertion-signed', True),
        )

        form.add(
            CheckboxWidget,
            'idp-manage-user-attributes',
            title=_('IdP manage user attributes'),
            value=get_cfg('sp', {}).get('idp-manage-user-attributes', False),
            **hobo_kwargs(),
        )

        form.add(
            CheckboxWidget,
            'idp-manage-roles',
            title=_('IdP manage roles'),
            value=get_cfg('sp', {}).get('idp-manage-roles', False),
            **hobo_kwargs(),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if x509utils.can_generate_rsa_key_pair():
            form.add_submit('generate_rsa', _('Generate signing and encryption key pairs'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if form.get_widget('generate_rsa') and form.get_widget('generate_rsa').parse():
            result = self.sp_save(form)
            if result:
                form.set_error(*result)
            else:
                self.generate_rsa_keypair()
                return redirect('')
        if form.is_submitted() and not form.has_errors():
            result = self.sp_save(form)
            if result:
                form.set_error(*result)
            else:
                return redirect('.')

        get_response().set_title(_('Service Provider Configuration'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Service Provider Configuration')
        r += form.render()
        return r.getvalue()

    def write_sp_metadatas(
        self,
        signing_pem_key,
        private_signing_pem_key,
        encryption_pem_key,
        private_encryption_pem_key,
        saml2_metadata,
    ):
        '''Write SP metadatas, that key files and metadata files'''
        dir = get_publisher().app_dir
        if signing_pem_key:
            privatekey_fn = os.path.join(dir, 'private-key.pem')
            publickey_fn = os.path.join(dir, 'public-key.pem')
            atomic_write(publickey_fn, force_bytes(signing_pem_key))
            atomic_write(privatekey_fn, force_bytes(private_signing_pem_key))
        if encryption_pem_key:
            encryption_privatekey_fn = os.path.join(dir, 'encryption-private-key.pem')
            encryption_publickey_fn = os.path.join(dir, 'encryption-public-key.pem')
            atomic_write(encryption_publickey_fn, force_bytes(encryption_pem_key))
            atomic_write(encryption_privatekey_fn, force_bytes(private_encryption_pem_key))

        saml2_metadata_fn = os.path.join(dir, 'saml2-metadata.xml')
        atomic_write(saml2_metadata_fn, force_bytes(saml2_metadata))

    def configure_sp_metadatas(
        self, cfg_sp, signing_pem_key, private_signing_pem_key, encryption_pem_key, private_encryption_pem_key
    ):
        if x509utils.can_generate_rsa_key_pair():
            if signing_pem_key and not x509utils.check_key_pair_consistency(
                signing_pem_key, private_signing_pem_key
            ):
                return ('publickey', _('Signing key pair is invalid'))
            if encryption_pem_key and not x509utils.check_key_pair_consistency(
                encryption_pem_key, private_encryption_pem_key
            ):
                return ('encryption_publickey', _('Encryption key pair is invalid'))
        if signing_pem_key:
            cfg_sp['publickey'] = 'public-key.pem'
            cfg_sp['privatekey'] = 'private-key.pem'
        if encryption_pem_key:
            cfg_sp['encryption_privatekey'] = 'encryption-private-key.pem'
            cfg_sp['encryption_publickey'] = 'encryption-public-key.pem'

        cfg_sp['saml2_metadata'] = 'saml2-metadata.xml'
        saml2_metadata = self.get_saml2_metadata(cfg_sp, signing_pem_key, encryption_pem_key)

        self.write_sp_metadatas(
            signing_pem_key,
            private_signing_pem_key,
            encryption_pem_key,
            private_encryption_pem_key,
            saml2_metadata,
        )
        get_publisher().write_cfg()
        return None

    def sp_save(self, form):
        get_publisher().reload_cfg()
        cfg_sp = get_cfg('sp', {})
        get_publisher().cfg['sp'] = cfg_sp
        for k in (
            'organization_name',
            'saml2_providerid',
            'saml2_base_url',
            'authn-request-signed',
            'want-assertion-signed',
            'idp-manage-user-attributes',
            'idp-manage-roles',
        ):
            if form.get_widget(k):
                cfg_sp[k] = form.get_widget(k).parse()

        def get_key(name):
            try:
                return form.get_widget(name).parse().fp.read()
            except Exception:
                return None

        signing_pem_key = get_key('publickey')
        private_signing_pem_key = get_key('privatekey')
        encryption_pem_key = get_key('encryption_publickey')
        private_encryption_pem_key = get_key('encryption_privatekey')

        return self.configure_sp_metadatas(
            cfg_sp, signing_pem_key, private_signing_pem_key, encryption_pem_key, private_encryption_pem_key
        )

    def get_saml2_metadata(self, sp_config, signing_pem_key, encryption_pem_key):
        meta = saml2utils.Metadata(
            publisher=get_publisher(), config=sp_config, provider_id=sp_config['saml2_providerid']
        )
        return meta.get_saml2_metadata(signing_pem_key, encryption_pem_key, do_sp=True)

    def identities(self):
        form = Form(enctype='multipart/form-data')
        identities_cfg = get_cfg('saml_identities', {})

        form.add(
            StringWidget,
            'registration-url',
            title=_('Registration URL'),
            hint=_(
                'URL on Identity Provider where users can register '
                'an account. Available variable: next_url.'
            ),
            value=identities_cfg.get('registration-url', ''),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.identities_submit(form)
            return redirect('.')

        get_response().breadcrumb.append(('identities', _('Identities Interface')))
        get_response().set_title(_('Identities Interface'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Identities Interface')
        r += form.render()
        return r.getvalue()

    def identities_submit(self, form):
        cfg_submit(form, 'saml_identities', ('registration-url',))


class MethodUserDirectory(Directory):
    _q_exports = []

    def __init__(self, user):
        self.user = user

    def get_actions(self):
        return []


class IdPAuthMethod(AuthMethod):
    key = 'idp'
    description = _('SAML identity provider')
    method_directory = MethodDirectory
    method_admin_directory = MethodAdminDirectory
    method_user_directory = MethodUserDirectory

    def login(self):
        idps = get_cfg('idp', {})

        # there is only one visible IdP, perform login automatically on
        # this one.
        server = misc.get_lasso_server()
        for x in sorted(server.providerIds):
            key_provider_id = misc.get_provider_key(x)
            if not idps.get(key_provider_id, {}).get('hide', False):
                saml = get_publisher().root_directory_class.saml
                return saml.perform_login(x)
