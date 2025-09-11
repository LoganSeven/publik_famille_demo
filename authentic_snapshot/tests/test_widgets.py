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
# authentic2

from pyquery import PyQuery

from authentic2.widgets import DatalistTextInput, DateTimeWidget, DateWidget, NewPasswordInput, TimeWidget


def test_datetimepicker_init_and_render_no_locale():
    DateTimeWidget().render('wt', '2019/12/12 12:34:34')
    DateWidget().render('wt', '2019/12/12')
    TimeWidget().render('wt', '12:34:34')


def test_datetimepicker_init_and_render_fr(french_translation):
    DateTimeWidget().render('wt', '2019/12/12 12:34:34')
    DateWidget().render('wt', '2019/12/12')
    TimeWidget().render('wt', '12:34:34')


def test_datalisttextinput_init_and_render():
    data = ['blue', 'red', 'green']
    widget = DatalistTextInput(name='foo', data=data)
    html = widget.render(name='bar', value='examplevalue')
    query = PyQuery(html)

    textinput = query.find('input')
    assert textinput.attr('name') == 'bar'
    assert textinput.attr('value') == 'examplevalue'
    assert textinput.attr('list') == 'list__foo'

    datalist = query.find('datalist')
    assert datalist.attr('id') == 'list__foo'
    for option in datalist.find('option'):
        assert len(option.values()) == 1
        assert option.values()[0] in data
        data.remove(option.values()[0])
    assert not data


def test_new_password_input(db):
    widget = NewPasswordInput()
    html = widget.render('foo', 'bar')
    query = PyQuery(html)

    textinput = query.find('input')
    assert textinput.attr('data-min-strength') is None

    widget = NewPasswordInput()
    widget.min_strength = 3
    html = widget.render('foo', 'bar')
    query = PyQuery(html)

    textinput = query.find('input')
    assert textinput.attr('data-min-strength') == '3'
