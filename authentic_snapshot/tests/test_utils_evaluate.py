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


import ast
from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from authentic2.utils.evaluate import (
    BaseExpressionValidator,
    ConditionValidator,
    ExpressionError,
    HTTPHeaders,
    condition_validator,
    evaluate_condition,
    make_condition_context,
)
from authentic2.utils.template import evaluate_condition_template


def test_base():
    v = BaseExpressionValidator()

    #    assert v('1')[0] is False
    #    assert v('\'a\'')[0] is False
    #    assert v('x')[0] is False

    v = BaseExpressionValidator(authorized_nodes=[ast.Num, ast.Str])

    assert v('1')
    assert v('\'a\'')

    # code object is cached
    assert v('1') is v('1')
    assert v('\'a\'') is v('\'a\'')
    with pytest.raises(ExpressionError):
        assert v('x')


def test_condition_validator_klass():
    v = ConditionValidator()
    assert v('x < 2 and y == \'u\' or \'a\' in z')
    with pytest.raises(ExpressionError) as raised:
        v('a and _b')
    assert raised.value.code == 'invalid-variable'
    assert raised.value.text == '_b'

    with pytest.raises(ExpressionError) as raised:
        v('a + b')
    assert str(raised.value) == '[\'expression "a + b" is forbidden\']'

    with pytest.raises(ExpressionError) as raised:
        v('1 + 2')

    v('a[1]')

    v('a[\'xx\']')

    with pytest.raises(ExpressionError, match='MUST be a constant'):
        v('a[1:2]')

    with pytest.raises(ExpressionError, match='MUST be a constant'):
        v('headers[headers]')

    assert v('func(a, b, 1, \'x\')')
    with pytest.raises(ExpressionError):
        assert v('func(a[0], b(c), 1, \'x\')')


def test_evaluate_condition(rf):
    assert evaluate_condition('False') is False
    assert evaluate_condition('True') is True
    assert evaluate_condition('not True') is False
    assert evaluate_condition('True and False') is False
    assert evaluate_condition('True or False') is True
    assert evaluate_condition('a or b', ctx=dict(a=True, b=False)) is True
    assert evaluate_condition('a < 1', ctx=dict(a=0)) is True
    with pytest.raises(ExpressionError) as exc_info:
        evaluate_condition('a < 1')
    assert exc_info.value.code == 'undefined-variable'
    assert evaluate_condition('a < 1', on_raise=False) is False


def test_evaluate_condition_log_exception(caplog):
    assert evaluate_condition('a < 1', on_raise=False) is False
    assert 'evaluate_condition:' in caplog.records[0].message


def test_http_headers(rf):
    request = rf.get('/', HTTP_X_ENTROUVERT='1')
    headers = HTTPHeaders(request)
    assert evaluate_condition('"X-Entrouvert" in headers', ctx={'headers': headers}) is True
    assert evaluate_condition('headers["X-Entrouvert"]', ctx={'headers': headers}) == '1'


def test_dnsbl_ok():
    from authentic2.utils.evaluate import dnsbl

    with mock.patch(
        'authentic2.utils.evaluate._resolver_resolve', return_value=[mock.Mock(address='127.0.0.2')]
    ):
        assert (
            evaluate_condition(
                "remote_addr in dnsbl('example.com')", ctx={'dnsbl': dnsbl, 'remote_addr': '1.2.3.4'}
            )
            is True
        )

    with mock.patch(
        'authentic2.utils.evaluate._resolver_resolve',
        return_value=[mock.Mock(address='2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF')],
    ):
        assert (
            evaluate_condition(
                "remote_addr in dnsbl('example.com')",
                ctx={'dnsbl': dnsbl, 'remote_addr': '2001:db8:3333:4444:CCCC:DDDD:EEEE:EEEE'},
            )
            is True
        )


def test_dnsbl_nok():
    from dns.resolver import NXDOMAIN

    from authentic2.utils.evaluate import dnsbl

    with mock.patch('authentic2.utils.evaluate._resolver_resolve', side_effect=NXDOMAIN):
        assert (
            evaluate_condition(
                "remote_addr in dnsbl('example.com')", ctx={'dnsbl': dnsbl, 'remote_addr': '1.2.3.4'}
            )
            is False
        )

    with mock.patch('authentic2.utils.evaluate._resolver_resolve', side_effect=NXDOMAIN):
        assert (
            evaluate_condition(
                "remote_addr in dnsbl('example.com')",
                ctx={'dnsbl': dnsbl, 'remote_addr': '2001:db8:3333:4444:CCCC:DDDD:EEEE:EEEE'},
            )
            is False
        )


def test_sp_next_url():
    assert evaluate_condition('"foo" in sp_next_url', ctx={'sp_next_url': '/foobar'}) is True
    assert evaluate_condition('"baz" in sp_next_url', ctx={'sp_next_url': '/foobar'}) is False
    assert (
        evaluate_condition('"foo" in sp_next_url', ctx={'sp_next_url': make_condition_context(request=None)})
        is False
    )


def test_evaluate_condition_template():
    assert evaluate_condition_template('foo == "bar"', {'foo': 'bar'}) is True
    assert evaluate_condition_template('foo != "bar"', {'foo': 'bar'}) is False


def test_condition_validator():
    with pytest.raises(ValidationError) as raised:
        condition_validator('2 ** 3')
    assert raised.value.messages == ['expression "2 ** 3" is forbidden']
