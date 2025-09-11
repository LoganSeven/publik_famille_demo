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
import logging
import re

import dns.exception
import dns.resolver
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address

try:
    from functools import lru_cache
except ImportError:
    from django.utils.lru_cache import lru_cache

from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


class HTTPHeaders:
    def __init__(self, request):
        self.request = request

    def __contains__(self, header):
        meta_header = 'HTTP_' + header.replace('-', '_').upper()
        return meta_header in self.request.META

    def __getitem__(self, header):
        meta_header = 'HTTP_' + header.replace('-', '_').upper()
        return self.request.META.get(meta_header)


class Unparse(ast.NodeVisitor):
    def visit_Name(self, node):
        return node.id


class ExpressionError(ValidationError):
    colummn = None
    node = None
    text = None

    def __init__(self, message, code=None, params=None, node=None, column=None, text=None):
        super().__init__(message, code=code, params=params)
        if hasattr(node, 'col_offset'):
            self.set_node(node)
        if column is not None:
            self.column = column
        if text is not None:
            self.text = text

    def set_node(self, node):
        assert hasattr(node, 'col_offset'), 'only node with col_offset attribute'
        self.node = node
        self.column = node.col_offset
        self.text = Unparse().visit(node)


def is_valid_hostname(hostname):
    if hostname[-1] == '.':
        # strip exactly one dot from the right, if present
        hostname = hostname[:-1]
    if len(hostname) > 253:
        return False

    labels = hostname.split('.')

    # the TLD must be not all-numeric
    if re.match(r'[0-9]+$', labels[-1]):
        return False

    allowed = re.compile(r'(?!-)[a-z0-9-]{1,63}(?<!-)$', re.IGNORECASE)
    return all(allowed.match(label) for label in labels)


def _resolver_resolve(domain):
    try:
        method = dns.resolver.resolve
    except AttributeError:
        # support for dnspython 2.0.0 on bullseye, prevent deprecation warning on later versions
        method = dns.resolver.query
    return method(domain, 'A', lifetime=1)


def check_dnsbl(dnsbl, remote_addr):
    domain = '.'.join(reversed(remote_addr.split('.'))) + '.' + dnsbl
    exception = None
    log = logger.debug
    try:
        answers = _resolver_resolve(domain)
        result = any(answer.address for answer in answers)
    except dns.resolver.NXDOMAIN as e:
        exception = e
        result = False
    except dns.resolver.NoAnswer as e:
        exception = e
        result = False
    except dns.exception.DNSException as e:
        exception = e
        log = logger.warning
        result = False
    log('utils: dnsbl lookup of "%s", result=%s exception=%s', domain, result, exception)
    return result


class DNSBL:
    def __init__(self, domain):
        if not is_valid_hostname(domain):
            raise ValueError('%s is not a valid domain name' % domain)
        self.domain = domain

    def __contains__(self, remote_addr):
        if not remote_addr or not isinstance(remote_addr, str):
            return False
        validate_ipv46_address(remote_addr)
        return check_dnsbl(self.domain, remote_addr)


def dnsbl(domain):
    return DNSBL(domain)


class BaseExpressionValidator(ast.NodeVisitor):
    authorized_nodes = []
    forbidden_nodes = []

    def __init__(self, authorized_nodes=None, forbidden_nodes=None):
        if authorized_nodes is not None:
            self.authorized_nodes = authorized_nodes
        if forbidden_nodes is not None:
            self.forbidden_nodes = forbidden_nodes

    def generic_visit(self, node):
        # generic node class checks
        ok = False
        if not isinstance(node, ast.Expression):
            for klass in self.authorized_nodes:
                if isinstance(node, klass):
                    ok = True
                    break
            for klass in self.forbidden_nodes:
                if isinstance(node, klass):
                    ok = False
        else:
            ok = True
        if not ok:
            raise ExpressionError(
                _('expression "%(expression)s" is forbidden'),
                node=node,
                code='forbidden-expression',
                params={'expression': ast.unparse(node)},
            )

        # specific node class check
        node_name = node.__class__.__name__
        check_method = getattr(self, 'check_' + node_name, None)
        if check_method:
            check_method(node)

        # now recurse on subnodes
        try:
            return super().generic_visit(node)
        except ExpressionError as e:
            # for errors in non expr nodes (so without a col_offset attribute,
            # set the nearer expr node as the node of the error
            if e.node is None and hasattr(node, 'col_offset'):
                e.set_node(node)
            expression = ast.unparse(node)
            if expression:
                if not e.text:
                    e.text = expression
                if not e.params:
                    e.params = {}
                if not e.params.get('expression'):
                    e.params['expression'] = expression
            raise e

    @lru_cache(maxsize=1024)
    def __call__(self, expression):
        try:
            tree = ast.parse(expression, mode='eval')
        except SyntaxError as e:
            raise ExpressionError(
                _('could not parse expression: %s') % e,
                code='parsing-error',
                column=e.offset,
                text=expression,
            )
        try:
            self.visit(tree)
        except ExpressionError as e:
            if e.text is None:
                e.text = expression
            if not e.params:
                e.params = {}
            if 'expression' not in e.params:
                e.params['expression'] = expression
            raise e
        return compile(tree, expression, mode='eval')


# python 3.8 introduced ast.Constant to replace Num, Str, Bytes and NameConstant (True, False, None)
CONSTANT_CLASSES = (ast.Constant,)


class ConditionValidator(BaseExpressionValidator):
    """
    Only authorize :
    - direct variable references, without underscore in them,
    - num and str constants,
    - boolean expressions with all operators,
    - unary operator expressions with all operators,
    - if expressions (x if y else z),
    - compare expressions with all operators.
    - subscript of direct variable reference.
    - calls to simple names with simple literal or variable values

    Are implicitely forbidden:
    - binary expressions (so no "'aaa' * 99999999999" or 233333333333333233**2232323233232323 bombs),
    - lambda,
    - literal list, tuple, dict and sets,
    - comprehensions (list, dict and set),
    - generators,
    - yield,
    - others calls,
    - Repr node (i dunno what it is),
    - attribute access,
    """

    authorized_nodes = [
        ast.Load,
        ast.Name,
        ast.Num,
        ast.Str,
        ast.BoolOp,
        ast.UnaryOp,
        ast.IfExp,
        ast.Subscript,
        ast.Index,
        ast.unaryop,
        ast.boolop,
        ast.cmpop,
        ast.Compare,
        ast.Call,
    ]

    def __init__(self, authorized_nodes=None, forbidden_nodes=None):
        super().__init__(authorized_nodes=authorized_nodes, forbidden_nodes=forbidden_nodes)
        self.authorized_nodes.append(ast.NameConstant)

    def check_Name(self, node):
        if node.id.startswith('_'):
            raise ExpressionError(_('name must not start with a _'), code='invalid-variable', node=node)

    def check_Call(self, node):
        if isinstance(node.func, ast.Name) and all(self.validate_call_arg(arg) for arg in node.args):
            return
        raise ExpressionError(_('call is invalid'), code='invalid-call', node=node)

    def validate_call_arg(self, node):
        # check node is constant or string
        return self.is_constant(node) or isinstance(node, ast.Name)

    def is_constant(self, node):
        return isinstance(node, CONSTANT_CLASSES)

    def check_Subscript(self, node):
        # check subscript are constant number or strings
        ok = True
        ok = isinstance(node.slice, CONSTANT_CLASSES)
        if not ok:
            raise ExpressionError(
                _('subscript index MUST be a constant'), code='invalid-subscript', node=node
            )


validate_condition = ConditionValidator()


def condition_validator(value):
    validate_condition(value)


condition_safe_globals = {
    '__builtins__': {
        'True': True,
        'False': False,
    }
}


def evaluate_condition(expression, ctx=None, validator=None, on_raise=None):
    try:
        code = (validator or validate_condition)(expression)
        try:
            return eval(code, condition_safe_globals, ctx or {})  # pylint: disable=eval-used
        except NameError as e:
            # NameError does not report the column of the name reference :/
            raise ExpressionError(
                _('variable is not defined: %s') % e, code='undefined-variable', text=expression, column=0
            )
    except Exception as e:
        if on_raise is not None:
            logger.exception('evaluate_condition: %s ctx=%r', expression, ctx)
            return on_raise
        raise e


def make_condition_context(*, request=None, **kwargs):
    '''Helper to make a condition context'''
    ctx = {
        'dnsbl': dnsbl,
        'sp_next_url': '',
    }
    if request:
        ctx['headers'] = HTTPHeaders(request)
        ctx['remote_addr'] = request.META.get('REMOTE_ADDR')
        if hasattr(request, 'session') and 'sp_next_url' in request.session:
            # mellon service providers put their next url in a dedicated assertion tag
            ctx['sp_next_url'] = request.session['sp_next_url']
    ctx.update(kwargs)
    return ctx
