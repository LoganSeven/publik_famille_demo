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

from quixote import get_publisher, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from .. import _, audit, get_cfg
from ..admin.cfg import cfg_submit, hobo_kwargs
from ..form import CheckboxWidget, Form, StringWidget, TextWidget, WidgetList


class EmailsDirectory(Directory):
    emails_dict = {}
    _q_exports = ['', 'options']

    @classmethod
    def register(
        cls,
        key,
        description,
        hint=None,
        enabled=False,
        default_subject=None,
        default_body=None,
        category=None,
        condition=None,
    ):
        if key in cls.emails_dict:
            return

        cls.emails_dict[key] = {
            'description': description,
            'default_subject': default_subject,
            'default_body': default_body,
            'hint': hint,
            'enabled': enabled,
            'category': category,
            'condition': condition,
        }

    @classmethod
    def is_enabled(cls, key):
        emails_cfg = get_cfg('emails', {})
        return emails_cfg.get('email-%s_enabled' % key, True)

    @classmethod
    def get_subject(cls, email_key):
        emails_cfg = get_cfg('emails', {})
        cfg_key = 'email-%s' % email_key
        default_subject = cls.emails_dict[email_key].get('default_subject')
        real_subject = emails_cfg.get(cfg_key + '_subject') or str(default_subject)
        return real_subject

    @classmethod
    def get_body(cls, email_key):
        emails_cfg = get_cfg('emails', {})
        cfg_key = 'email-%s' % email_key
        default_body = cls.emails_dict[email_key].get('default_body')
        real_body = emails_cfg.get(cfg_key) or str(default_body)
        return real_body

    def options(self):
        form = Form(enctype='multipart/form-data')
        disabled_screens = '%s,%s' % (
            get_publisher().get_site_option('settings-disabled-screens') or '',
            get_publisher().get_site_option('settings-hidden-screens') or '',
        )
        disabled_smtp_options = 'smtp' in [x.strip() for x in disabled_screens.split(',')]
        emails = get_cfg('emails', {})
        if not disabled_smtp_options:
            form.add(
                StringWidget,
                'smtp_server',
                title=_('SMTP Server'),
                required=False,
                value=emails.get('smtp_server', ''),
            )
            form.add(
                StringWidget,
                'smtp_login',
                title=_('SMTP Login'),
                required=False,
                value=emails.get('smtp_login', ''),
            )
            form.add(
                StringWidget,
                'smtp_password',
                title=_('SMTP Password'),
                required=False,
                value=emails.get('smtp_password', ''),
            )
        form.add(
            StringWidget,
            'from',
            title=_('Email Sender'),
            required=True,
            value=emails.get('from'),
            **hobo_kwargs(),
        )
        form.add(
            StringWidget,
            'reply_to',
            title=_('Reply-To Address'),
            required=False,
            value=emails.get('reply_to'),
        )
        form.add(
            TextWidget,
            'footer',
            title=_('Email Footer'),
            cols=70,
            rows=5,
            required=False,
            value=emails.get('footer'),
            **hobo_kwargs(),
        )
        form.add(
            CheckboxWidget,
            'check_domain_with_dns',
            title=_('Check DNS for domain name'),
            value=emails.get('check_domain_with_dns', True),
            hint=_('Use a DNS request to check domain names used in email fields'),
        )
        form.add(
            WidgetList,
            'well_known_domains',
            title=_('Domains to check for spelling errors'),
            element_type=StringWidget,
            element_kwargs={'render_br': False},
            value=get_publisher().get_email_well_known_domains(),
        )
        form.add(
            WidgetList,
            'valid_known_domains',
            title=_('Domains that should not be considered spelling errors'),
            element_type=StringWidget,
            element_kwargs={'render_br': False},
            value=get_publisher().get_email_valid_known_domains(),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Emails'))
            get_response().breadcrumb.append(('options', _('General Options')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('General Options')
            mail_redirection = get_cfg('debug', {}).get('mail_redirection')
            if mail_redirection:
                r += htmltext('<div class="infonotice">')
                r += htmltext('<p>')
                r += str(_('Warning: all emails are sent to <%s>') % mail_redirection)
                r += htmltext(' <a href="../debug_options">%s</a>') % _('Debug Options')
                r += htmltext('</p>')
                r += htmltext('</div>')
            r += form.render()
            return r.getvalue()

        cfg_submit(
            form,
            'emails',
            [
                'smtp_server',
                'smtp_login',
                'smtp_password',
                'from',
                'reply_to',
                'footer',
                'check_domain_with_dns',
                'well_known_domains',
                'valid_known_domains',
            ],
        )
        return redirect('.')

    def _q_index(self):
        get_response().set_title(_('Emails'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Emails')

        r += htmltext('<ul>')
        r += htmltext('<li><a href="options">%s</a></li>') % _('General Options')
        r += htmltext('</ul>')

        emails_dict = {
            x: y for x, y in self.emails_dict.items() if not y.get('condition') or y['condition']()
        }

        categories = {}
        for k, v in emails_dict.items():
            if v.get('category'):
                translated_category = v.get('category')
            else:
                translated_category = _('Miscellaneous')
            if translated_category not in categories:
                categories[translated_category] = []
            categories[translated_category].append(k)

        for category_key in sorted(categories.keys()):
            if len(categories) > 1:
                r += htmltext('<h3>%s</h3>') % category_key

            keys = categories.get(category_key)
            keys.sort(key=lambda x: emails_dict[x]['description'])
            r += htmltext('<ul>')
            for email_key in keys:
                email_values = emails_dict[email_key]
                r += htmltext('<li><a href="%s">%s</a></li>') % (email_key, email_values['description'])
            r += htmltext('</ul>')

        r += htmltext('<p>')
        r += htmltext('<a href="..">%s</a>') % _('Back')
        r += htmltext('</p>')
        return r.getvalue()

    def email(self, email_key, email_label, hint=None, check_template=None, enabled=True):
        emails_cfg = get_cfg('emails', {})
        cfg_key = 'email-%s' % email_key

        default_subject = self.emails_dict[email_key].get('default_subject')
        default_body = self.emails_dict[email_key].get('default_body')

        displayed_subject = emails_cfg.get(cfg_key + '_subject') or default_subject
        displayed_body = emails_cfg.get(cfg_key) or default_body

        form = Form(enctype='multipart/form-data')
        form.add(
            CheckboxWidget,
            cfg_key + '_enabled',
            title=_('Enabled Email'),
            value=emails_cfg.get(cfg_key + '_enabled', True),
            default=enabled,
        )
        form.add(StringWidget, cfg_key + '_subject', title=_('Subject'), value=displayed_subject, size=40)
        form.add(TextWidget, cfg_key, title=email_label, value=displayed_body, cols=80, rows=10, hint=hint)
        form.add_submit('submit', _('Submit'))
        if displayed_subject != default_subject or displayed_body != default_body:
            form.add_submit('restore-default', _('Restore default email'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'restore-default':
            self.email_submit(None, email_key)
            return redirect('')

        if form.is_submitted() and not form.has_errors():
            if self.email_submit(form, email_key, check_template):
                return redirect('.')
            form.set_error(cfg_key, _('Invalid template'))

        get_response().breadcrumb.append((email_key, email_label))
        get_response().set_title(_('Emails'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s - %s</h2>') % (_('Email'), email_label)
        r += form.render()
        r += get_publisher().substitutions.get_substitution_html_table(
            intro=_('The email subject and body can reference variables from the table below:')
        )
        return r.getvalue()

    def email_submit(self, form, email_key, check_template=None):
        get_publisher().reload_cfg()
        cfg_key = 'email-%s' % email_key
        emails_cfg = get_cfg('emails', {})
        if form:
            template = form.get_widget(cfg_key).parse()
            if check_template and not check_template(template):
                return False

            default_subject = self.emails_dict[email_key].get('default_subject')
            default_body = self.emails_dict[email_key].get('default_body')

            if template == default_body:
                template = None

            subject = form.get_widget(cfg_key + '_subject').parse()
            if subject == default_subject:
                subject = None

            emails_cfg[str(cfg_key)] = template
            emails_cfg[str(cfg_key + '_enabled')] = form.get_widget(cfg_key + '_enabled').parse()
            emails_cfg[str(cfg_key + '_subject')] = subject
        else:
            emails_cfg[str(cfg_key)] = None
        audit('settings', cfg_key='emails', cfg_email_key=email_key)
        get_publisher().cfg['emails'] = emails_cfg
        get_publisher().write_cfg()
        return True

    def _q_lookup(self, component):
        if component not in self.emails_dict:
            return None

        return self.email(
            component,
            self.emails_dict[component]['description'],
            self.emails_dict[component]['hint'],
            self.emails_dict[component]['enabled'],
        )

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('emails/', _('Emails')))
        return Directory._q_traverse(self, path)
