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

from quixote import get_publisher, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, audit, ezt, get_cfg
from wcs.qommon.form import Form, WysiwygTextWidget
from wcs.qommon.template import Template


class TextsDirectory(Directory):
    texts_dict = {}
    _q_exports = ['']

    @classmethod
    def get_html_text(cls, key, vars=None):
        texts_cfg = get_cfg('texts', {})
        text = texts_cfg.get('text-' + key)
        if not text:
            default = cls.texts_dict.get(key, {}).get('default')
            if not default:
                filepath = os.path.join(get_publisher().DATA_DIR, 'texts', '%s.html' % key)
                if os.path.exists(filepath):
                    with open(filepath) as fd:
                        return htmltext(fd.read())
                return ''
            text = str(default)  # make sure translation is applied

        if not text.startswith('<'):
            text = '<p>%s</p>' % text

        subst_vars = get_publisher().substitutions.get_context_variables()
        if vars:
            subst_vars.update(vars)

        text = Template(text, ezt_format=ezt.FORMAT_HTML).render(subst_vars)
        return htmltext('<div class="text-%s">%s</div>' % (key, text))

    @classmethod
    def register(cls, key, description, hint=None, default=None, wysiwyg=True, category=None, condition=None):
        # the wysiwyg is not actually used, it's always considered True, it's
        # kept for backward compatibility with callers.
        if key in cls.texts_dict:
            return

        cls.texts_dict[key] = {
            'description': description,
            'hint': hint,
            'default': default,
            'category': category,
            'condition': condition,
        }

    def _q_index(self):
        get_response().set_title(_('Texts'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Custom Texts')

        texts_dict = {x: y for x, y in self.texts_dict.items() if not y.get('condition') or y['condition']()}

        categories = {}
        for k, v in texts_dict.items():
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
            keys.sort(key=lambda x: texts_dict[x]['description'])
            r += htmltext('<ul>')
            for text_key in keys:
                text_values = texts_dict[text_key]
                r += htmltext('<li><a href="%s">%s</a></li>') % (text_key, text_values['description'])
            r += htmltext('</ul>')

        r += htmltext('<p>')
        r += htmltext('<a href="..">%s</a>') % _('Back')
        r += htmltext('</p>')
        return r.getvalue()

    def text(self, text_key, text_label, hint=None, check_template=None):
        texts_cfg = get_cfg('texts', {})
        cfg_key = 'text-%s' % text_key

        default_text = self.texts_dict.get(text_key, {}).get('default')
        if not default_text:
            filepath = os.path.join(get_publisher().DATA_DIR, 'texts', '%s.html' % text_key)
            if os.path.exists(str(filepath)):
                with open(str(filepath)) as fd:
                    default_text = fd.read()

        displayed_text = texts_cfg.get(cfg_key) or default_text

        form = Form(enctype='multipart/form-data')
        form.add(
            WysiwygTextWidget, cfg_key, title=text_label, value=displayed_text, cols=80, rows=10, hint=hint
        )
        form.add_submit('submit', _('Submit'))
        if displayed_text != default_text:
            form.add_submit('restore-default', _('Restore default text'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'restore-default':
            self.text_submit(None, text_key)
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            if self.text_submit(form, text_key, check_template):
                return redirect('.')
            form.set_error(cfg_key, _('Invalid template'))

        get_response().breadcrumb.append((text_key, text_label))
        get_response().set_title(_('Texts'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s - %s</h2>') % (_('Text'), text_label)
        r += form.render()
        r += get_publisher().substitutions.get_substitution_html_table(
            intro=_('The text can reference variables from the table below:')
        )
        return r.getvalue()

    def text_submit(self, form, text_key, check_template=None):
        get_publisher().reload_cfg()

        texts_cfg = get_cfg('texts', {})
        cfg_key = 'text-%s' % text_key

        default_text = self.texts_dict.get(text_key, {}).get('default')

        texts_cfg = get_cfg('texts', {})
        if form:
            template = form.get_widget(cfg_key).parse()
            if check_template and not check_template(template):
                return False
            if template != default_text:
                texts_cfg[str(cfg_key)] = template
            else:
                texts_cfg[str(cfg_key)] = None
        else:
            texts_cfg[str(cfg_key)] = None
        audit('settings', cfg_key='texts', cfg_text_key=text_key)
        get_publisher().cfg['texts'] = texts_cfg
        get_publisher().write_cfg()
        return True

    def _q_lookup(self, component):
        if component not in self.texts_dict:
            return None

        hint = self.texts_dict[component]['hint']
        return self.text(component, self.texts_dict[component]['description'], hint)

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('texts/', _('Texts')))
        return Directory._q_traverse(self, path)
