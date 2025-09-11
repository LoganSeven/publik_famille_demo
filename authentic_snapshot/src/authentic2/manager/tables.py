# pylint: skip-file

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

import django_tables2 as tables
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import CharField, OuterRef, Subquery
from django.db.models.expressions import RawSQL
from django.utils import html
from django.utils.safestring import SafeText
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from django_tables2.utils import A

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.middleware import StoreRequestMiddleware
from authentic2.models import AttributeValue, Service
from authentic2.utils.misc import get_password_authenticator
from authentic2_idp_oidc.models import OIDCAuthorization

User = get_user_model()


class Table(tables.Table):
    class Meta:
        row_attrs = {'data-pk': lambda record: record.pk}


class PermissionLinkColumn(tables.LinkColumn):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        original_link = self.link

        def link(content, table, record, **render_kwargs):
            view = getattr(table, 'view', None)
            if view and view.request:
                permission = '%s.view_%s' % (record._meta.app_label, record._meta.model_name)
                if not view.request.user.has_perm(permission, record):
                    return content
            return original_link(content, table=table, record=record, **render_kwargs)

        self.link = link


class VerifiableEmailColumn(tables.Column):
    def render(self, record):
        user = record
        verified = user.email_verified
        value = user.email
        if value and verified:
            return html.format_html('<span class="verified">{value}</span>', value=value)
        return value


class VerifiablePhoneNumberColumn(tables.Column):
    def render(self, record):
        user = record
        verified = user.phone_verified_on
        value = user.phone_id or '–'
        if value and verified:
            return html.format_html('<span class="verified">{value}</span>', value=value)
        return value

    def order(self, queryset, is_descending):
        queryset, dummy = super().order(queryset, is_descending)
        order_by = '-phone_id' if is_descending else 'phone_id'
        return queryset.order_by(order_by), True

    @property
    def header(self):
        if field := get_password_authenticator().phone_identifier_field:
            return field.label
        # call to parent property should not happen, column is excluded when phone authn is deactivated
        return super().header


class UserLinkColumn(PermissionLinkColumn):
    def render(self, record, value):
        value = super().render(record, value)
        if isinstance(record, User) and not record.is_active:
            value = html.format_html(
                '<span class="disabled">{value} ({disabled})</span>', value=value, disabled=_('disabled')
            )
        return value


class UserTable(Table):
    get_full_name = UserLinkColumn(
        verbose_name=_('User'),
        args=[A('pk')],
        order_by=('last_name', 'first_name', 'email', 'username'),
        text=lambda record: record.get_full_name(),
        attrs={'td': {'class': 'link'}},
    )
    username = tables.Column(
        attrs={
            'td': {'class': 'username'},
            'th': {'class': 'username orderable'},
        }
    )
    email = VerifiableEmailColumn()
    phone_id = VerifiablePhoneNumberColumn()
    ou = tables.Column()

    def __init__(self, *args, **kwargs):
        if not get_password_authenticator().is_phone_authn_active:
            kwargs['exclude'] = kwargs.setdefault('exclude', [])
            kwargs['exclude'].append('phone_id')
        else:
            attribute_id = get_password_authenticator().phone_identifier_field.id
            subquery = AttributeValue.objects.filter(
                object_id=OuterRef('pk'),
                attribute_id=attribute_id,
                content_type_id=ContentType.objects.get_for_model(User).id,
            ).values('content')
            kwargs['data'] = kwargs['data'].annotate(phone_id=Subquery(subquery, output_field=CharField()))
        return super().__init__(*args, **kwargs)

    class Meta(Table.Meta):
        model = User
        attrs = {'class': 'main clickable-rows', 'id': 'user-table'}
        fields = ('username', 'email', 'phone_id', 'first_name', 'last_name', 'ou')
        sequence = ('get_full_name', '...')
        empty_text = _('None')


class RoleMembersTable(UserTable):
    direct = tables.BooleanColumn(
        verbose_name=_('Direct member'), orderable=False, attrs={'td': {'class': 'direct'}}
    )
    via = tables.TemplateColumn(
        '{% for role in record.via %}<a href="{% url "a2-manager-role-members" pk=role.pk %}">{{ role'
        ' }}</a>{% if not forloop.last %}, {% endif %}{% endfor %}',
        verbose_name=_('Inherited from'),
        orderable=False,
        attrs={'td': {'class': 'via'}},
    )

    class Meta(UserTable.Meta):
        row_attrs = {'data-pk': lambda record: 'user-%s' % record.pk}


class UserOrRoleColumn(UserLinkColumn):
    def render(self, record, value):
        value = super().render(record, value)
        if isinstance(record, Role):
            value = html.format_html(_('Members of role {value}'), value=value)
        return value


class MixedUserRoleTable(Table):
    pk = UserOrRoleColumn(
        verbose_name=_('Members'),
        text=str,
        orderable=False,
        attrs={'td': {'class': 'name'}},
    )

    class Meta(Table.Meta):
        attrs = {'class': 'main clickable-rows', 'id': 'user-table'}
        row_attrs = {
            'data-pk': lambda record: '%s-%s' % ('user' if isinstance(record, User) else 'role', record.pk)
        }


class RoleTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-role-members',
        kwargs={'pk': A('pk')},
        accessor='name',
        verbose_name=_('label'),
        attrs={'td': {'class': 'name'}},
    )
    ou = tables.Column()
    slug = tables.Column(attrs={'td': {'class': 'slug'}})
    member_count = tables.Column(
        verbose_name=_('Direct member count'), orderable=False, attrs={'td': {'class': 'member_count'}}
    )

    def render_name(self, record, bound_column):
        content = bound_column.column.render(record, record.name)
        if not record.can_manage_members:
            content = SafeText('%s (%s)' % (content, _('LDAP')))
        return content

    class Meta(Table.Meta):
        model = Role
        attrs = {'class': 'main clickable-rows', 'id': 'role-table'}
        fields = ('name', 'slug', 'ou', 'member_count')
        order_by = ('name',)


class OUTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-ou-detail',
        kwargs={'pk': A('pk')},
        accessor='name',
        verbose_name=_('label'),
        attrs={'td': {'class': 'name'}},
    )
    slug = tables.Column(attrs={'td': {'class': 'slug'}})
    default = tables.BooleanColumn(attrs={'td': {'class': 'default'}})

    class Meta(Table.Meta):
        model = OrganizationalUnit
        attrs = {'class': 'main clickable-rows', 'id': 'ou-table'}
        fields = ('name', 'slug', 'default')
        empty_text = _('None')


class OuUserRolesTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-role-members',
        kwargs={'pk': A('pk')},
        accessor='name',
        verbose_name=_('label'),
        attrs={'td': {'class': 'name'}},
    )
    via = tables.TemplateColumn(
        '''{% for rel in record.via %}{{ rel.child }} {% if not forloop.last %}, {% endif %}{% endfor %}''',
        verbose_name=_('Inherited from'),
        orderable=False,
        attrs={'td': {'class': 'via'}},
    )
    member = tables.TemplateColumn(
        '{%% load i18n %%}<input class="role-member{%% if not record.member and record.via %%}'
        ' indeterminate{%% endif %%}" name="role-{{ record.pk }}" type="checkbox" {%% if record.member'
        ' %%}checked{%% endif %%} {%% if not record.has_perm %%}disabled title="{%% trans "%s" %%}"{%% endif'
        ' %%} {%% if not record.can_manage_members %%}disabled title="{%% trans "%s" %%}"{%% endif %%}/>'
        % (
            gettext_noop('You are not authorized to manage this role'),
            gettext_noop('This role is synchronised from LDAP, changing members is not allowed.'),
        ),
        verbose_name=_('Member'),
        order_by=('-member', 'name'),
        attrs={'td': {'class': 'member'}},
    )

    def render_name(self, record, bound_column):
        content = bound_column.column.render(record, record.name)
        if not record.can_manage_members:
            content = SafeText('%s (%s)' % (content, _('LDAP')))
        return content

    class Meta(Table.Meta):
        model = Role
        attrs = {'class': 'main clickable-rows', 'id': 'role-table'}
        fields = ('name', 'ou')
        empty_text = _('None')
        order_by = ('name',)


class UserRolesTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-role-members',
        kwargs={'pk': A('pk')},
        accessor='name',
        verbose_name=_('label'),
        attrs={'td': {'class': 'name'}},
    )
    ou = tables.Column()
    via = tables.TemplateColumn(
        '{% if not record.member %}{% for rel in record.child_relation.all %}'
        '{{ rel.child }} {% if not forloop.last %}, {% endif %}{% endfor %}{% endif %}',
        verbose_name=_('Inherited from'),
        orderable=False,
        attrs={'td': {'class': 'via'}},
    )

    def render_name(self, record, bound_column):
        content = bound_column.column.render(record, record.name)
        if not record.can_manage_members:
            content = SafeText('%s (%s)' % (content, _('LDAP')))
        return content

    class Meta(Table.Meta):
        model = Role
        attrs = {'class': 'main clickable-rows', 'id': 'role-table'}
        fields = ('name', 'ou')
        empty_text = _('None')
        order_by = ('name', 'ou')


class ServiceTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-service',
        kwargs={'service_pk': A('pk')},
        accessor='name',
        verbose_name=_('label'),
    )
    ou = tables.Column()
    slug = tables.Column()

    class Meta(Table.Meta):
        model = Service
        attrs = {'class': 'main clickable-rows', 'id': 'service-table'}
        fields = ('name', 'slug', 'ou')
        empty_text = _('None')
        order_by = ('ou', 'name', 'slug')


class ServiceRolesTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-role-members', kwargs={'pk': A('pk')}, accessor='name', verbose_name=_('label')
    )

    class Meta(Table.Meta):
        model = Role
        attrs = {'class': 'main clickable-rows', 'id': 'service-role-table'}
        fields = ('name',)
        empty_text = _('No access restriction. All users are allowed to connect to this service.')


class UserAuthorizationsTable(Table):
    client = tables.Column(orderable=False)
    created = tables.Column()
    expired = tables.Column()

    class Meta(Table.Meta):
        model = OIDCAuthorization
        attrs = {'class': 'main', 'id': 'user-authorizations-table'}
        fields = ('client', 'created', 'expired')
        empty_text = _('This user has not granted profile data access to any service yet.')


class InheritanceRolesTable(Table):
    name = tables.LinkColumn(
        viewname='a2-manager-role-members',
        kwargs={'pk': A('pk')},
        accessor='name',
        verbose_name=_('label'),
        attrs={'td': {'class': 'name'}},
    )
    via = tables.TemplateColumn(
        '''{% for rel in record.via %}{{ rel.name }}{% if not forloop.last %}, {% endif %}{% endfor %}''',
        verbose_name=_('Inherited from'),
        orderable=False,
        attrs={'td': {'class': 'via'}},
    )
    member = tables.TemplateColumn(
        '<input class="role-member{% if record.indeterminate %} indeterminate{% endif %}" name="role-{{ record.pk }}" '
        'type="checkbox" {% if record.checked %}checked{% endif %}/>',
        verbose_name='',
        orderable=False,
        attrs={'td': {'class': 'member'}},
    )

    class Meta(Table.Meta):
        model = Role
        attrs = {'class': 'main clickable-rows', 'id': 'inheritance-role-table'}
        fields = ('name', 'ou')
        empty_text = _('None')
