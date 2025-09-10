# w.c.s. - web application for online forms
# Copyright (C) 2005-2016  Entr'ouvert
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

import ast
import os

from quixote import get_publisher


class Script:
    ezt_call_mode = 'simple'

    def __init__(self, script_name):
        self.script_name = script_name + '.py'
        paths = [
            os.path.join(get_publisher().app_dir, 'scripts'),
            os.path.join(get_publisher().APP_DIR, 'scripts'),
        ]
        for path in paths:
            script_path = os.path.join(path, script_name + '.py')
            if os.path.exists(script_path):
                self.__file__ = script_path
                with open(script_path) as fd:
                    self.code = fd.read()
                break
        else:
            raise ValueError()

    @classmethod
    def get_substitution_variables(cls):
        return {'script': ScriptsSubstitutionProxy()}

    @property
    def __doc__(self):
        return ast.get_docstring(ast.parse(self.code, self.script_name))

    def __call__(self, *args):
        data = get_publisher().substitutions.get_context_variables(mode='static').copy()
        data['args'] = args
        data['__file__'] = self.__file__
        code_object = compile(self.code, self.script_name, 'exec')
        # noqa pylint: disable=eval-used
        eval(code_object, data)
        return data.get('result')


class ScriptsSubstitutionProxy:
    def __getattr__(self, attr):
        try:
            return Script(attr)
        except ValueError:
            raise AttributeError()
