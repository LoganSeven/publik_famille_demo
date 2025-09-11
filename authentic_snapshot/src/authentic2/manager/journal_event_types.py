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
import datetime
import hashlib
import json
import os

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import Role
from authentic2.apps.journal.models import EventTypeDefinition
from authentic2.apps.journal.utils import form_to_old_new
from authentic2.backends.ldap_backend import (
    LDAP_DEACTIVATION_REASON_NOT_PRESENT,
    LDAP_DEACTIVATION_REASON_OLD_SOURCE,
)
from authentic2.custom_user.models import DeletedUser
from authentic2.journal_event_types import EventTypeWithService, get_attributes_label
from authentic2.models import Service

User = get_user_model()


class ManagerUserCreation(EventTypeDefinition):
    name = 'manager.user.creation'
    label = _('user creation')

    @classmethod
    def record(cls, *, user, session, form, api=False):
        return super().record(user=user, session=session, references=[form.instance], api=api)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        # user journal page
        if context and context == user:
            return _('creation by administrator')
        elif user:
            # manager gloabal journal page
            return _('creation of user "%s"') % user
        return super().get_message(event, context)


class ManagerUserProfileEdit(EventTypeDefinition):
    name = 'manager.user.profile.edit'
    label = _('user profile edit')

    @classmethod
    def record(cls, *, user, session, form, api=False):
        return super().record(
            user=user, session=session, references=[form.instance], data=form_to_old_new(form), api=api
        )

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        new = event.get_data('new') or {}
        edited_attributes = ', '.join(get_attributes_label(new)) or ''
        if context and context == user:
            return _('edit by administrator (%s)') % edited_attributes
        elif user:
            return _('edit of user "{0}" ({1})').format(user, edited_attributes)
        return super().get_message(event, context)


class ManagerUserEmailChangeRequest(EventTypeDefinition):
    name = 'manager.user.email.change.request'
    label = _('email change request')

    @classmethod
    def record(cls, *, user, session, form):
        data = {
            'old_email': form.instance.email,
            'email': form.cleaned_data.get('new_email'),
        }
        return super().record(user=user, session=session, references=[form.instance], data=data)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        new_email = event.get_data('email')
        if context and context == user:
            return _('email change for email address "%s" requested by administrator') % new_email
        elif user:
            return _('email change of user "{0}" for email address "{1}"').format(user, new_email)
        return super().get_message(event, context)


class ManagerUserPasswordChange(EventTypeDefinition):
    name = 'manager.user.password.change'
    label = _('user password change')

    @classmethod
    def record(cls, *, user, session, form, api=False):
        cleaned_data = getattr(form, 'cleaned_data', {})
        data = {
            'generate_password': cleaned_data.get('generate_password', False),
            'send_mail': cleaned_data.get('send_mail', False),
        }
        return super().record(user=user, session=session, references=[form.instance], data=data, api=api)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        send_mail = event.get_data('send_mail')
        if context and context == user:
            if send_mail:
                return _('password change by administrator and notification by mail')
            else:
                return _('password change by administrator')
        elif user:
            if send_mail:
                return _('password change of user "%s" and notification by mail') % user
            else:
                return _('password change of user "%s"') % user
        return super().get_message(event, context)


class ManagerUserPasswordResetRequest(EventTypeDefinition):
    name = 'manager.user.password.reset.request'
    label = _('user password reset request')

    @classmethod
    def record(cls, *, user, session, target_user, api=False):
        return super().record(
            user=user, session=session, references=[target_user], data={'email': target_user.email}, api=api
        )

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        email = event.get_data('email')
        if context and context == user:
            return _('password reset request by administrator sent to "%s"') % email
        elif user:
            return _('password reset request of "{0}" sent to "{1}"').format(user, email)
        return super().get_message(event, context)


class ManagerUserPasswordChangeForce(EventTypeDefinition):
    name = 'manager.user.password.change.force'
    label = _('mandatory password change at next login set')

    @classmethod
    def record(cls, *, user, session, target_user, api=False):
        return super().record(user=user, session=session, references=[target_user], api=api)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        if context and context == user:
            return _('mandatory password change at next login set by administrator')
        elif user:
            return _('mandatory password change at next login set for user "%s"') % user
        return super().get_message(event, context)


class ManagerUserPasswordChangeUnforce(EventTypeDefinition):
    name = 'manager.user.password.change.unforce'
    label = _('mandatory password change at next login unset')

    @classmethod
    def record(cls, *, user, session, target_user):
        return super().record(user=user, session=session, references=[target_user])

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        if context and context == user:
            return _('mandatory password change at next login unset by administrator')
        elif user:
            return _('mandatory password change at next login unset for user "%s"') % user
        return super().get_message(event, context)


class ManagerUserActivation(EventTypeDefinition):
    name = 'manager.user.activation'
    label = _('user activation')

    @classmethod
    # pylint: disable=arguments-renamed
    def record(cls, *, target_user, user=None, session=None, origin=None, reason=None):
        data = {'origin': origin, 'reason': reason}
        return super().record(user=user, session=session, references=[target_user], data=data)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        reason = event.get_data('reason')
        if context and context == user:
            if reason == 'ldap-reactivation':
                return _('automatic activation because the associated LDAP account reappeared')
            else:
                return _('activation by administrator')
        elif user:
            if reason == 'ldap-reactivation':
                return (
                    _('automatic activation of user "%s" because the associated LDAP account reappeared')
                    % user
                )
            else:
                return _('activation of user "%s"') % user
        return super().get_message(event, context)


class ManagerUserDeactivation(EventTypeDefinition):
    name = 'manager.user.deactivation'
    label = _('user deactivation')

    @classmethod
    # pylint: disable=arguments-renamed
    def record(cls, *, target_user, user=None, session=None, origin=None, reason=None):
        data = {'reason': reason, 'origin': origin}
        return super().record(user=user, session=session, references=[target_user], data=data)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        reason = event.get_data('reason')
        if context and context == user:
            if reason == LDAP_DEACTIVATION_REASON_NOT_PRESENT:
                return _('automatic deactivation because the associated LDAP account does not exist anymore')
            elif reason == LDAP_DEACTIVATION_REASON_OLD_SOURCE:
                return _('automatic deactivation because the associated LDAP source has been deleted')
            else:
                return _('deactivation by administrator')
        elif user:
            if reason == LDAP_DEACTIVATION_REASON_NOT_PRESENT:
                return (
                    _(
                        'automatic deactivation of user "%s" because the associated LDAP account does not exist'
                        ' anymore'
                    )
                    % user
                )
            elif reason == LDAP_DEACTIVATION_REASON_OLD_SOURCE:
                return (
                    _(
                        'automatic deactivation of user "%s" because the associated LDAP source has been deleted'
                    )
                    % user
                )
            else:
                return _('deactivation of user "%s"') % user
        return super().get_message(event, context)


class ManagerUserDeletion(EventTypeDefinition):
    name = 'manager.user.deletion'
    label = _('user deletion')

    @classmethod
    def record(cls, *, user, session, target_user, api=False):
        return super().record(user=user, session=session, references=[target_user], api=api)

    @classmethod
    def get_message(cls, event, context):
        (user,) = event.get_typed_references((DeletedUser, User))
        if context and context == user:
            return _('deletion by administrator')
        elif user:
            return _('deletion of user "%s"') % user
        return super().get_message(event, context)


class ManagerUserSSOAuthorizationDeletion(EventTypeWithService):
    name = 'manager.user.sso.authorization.deletion'
    label = _('delete authorization')

    @classmethod
    def record(cls, *, user, session, service, target_user):
        return super().record(user=user, session=session, service=service, references=[target_user])

    @classmethod
    def get_message(cls, event, context):
        # first reference is to the service
        __, user = event.get_typed_references(None, (DeletedUser, User))
        service_name = cls.get_service_name(event)
        if context and context == user:
            return _('deletion of authorization of single sign on with "{service}" by administrator').format(
                service=service_name
            )
        elif user:
            return _('deletion of authorization of single sign on with "{service}" of user "{user}"').format(
                service=service_name,
                user=user,
            )
        return super().get_message(event, context)


class RoleEventsMixin(EventTypeDefinition):
    @classmethod
    def record(cls, *, user, role, session=None, references=None, data=None, api=False):
        references = references or []
        references = [role] + references
        data = data or {}
        data.update({'role_name': str(role), 'role_uuid': role.uuid})
        return super().record(
            user=user,
            session=session,
            references=references,
            data=data,
            api=api,
        )


class ManagerRoleCreation(RoleEventsMixin):
    name = 'manager.role.creation'
    label = _('role creation')

    @classmethod
    def get_message(cls, event, context):
        (role,) = event.get_typed_references(Role)
        role = role or event.get_data('role_name')
        if context != role:
            return _('creation of role "%s"') % role
        else:
            return _('creation')


class ManagerRoleEdit(RoleEventsMixin):
    name = 'manager.role.edit'
    label = _('role edit')

    @classmethod
    def record(cls, *, user, session, role, form, api=False):
        return super().record(user=user, session=session, role=role, data=form_to_old_new(form), api=api)

    @classmethod
    def get_message(cls, event, context):
        (role,) = event.get_typed_references(Role)
        role = role or event.get_data('role_name')
        new = event.get_data('new')
        edited_attributes = ', '.join(get_attributes_label(new)) or ''
        if context != role:
            return _('edit of role "{role}" ({change})').format(role=role, change=edited_attributes)
        else:
            return _('edit ({change})').format(change=edited_attributes)


class ManagerRoleDeletion(RoleEventsMixin):
    name = 'manager.role.deletion'
    label = _('role deletion')

    @classmethod
    def get_message(cls, event, context):
        (role,) = event.get_typed_references(Role)
        role = role or event.get_data('role_name')
        if context != role:
            return _('deletion of role "%s"') % role
        else:
            return _('deletion')


class ManagerRoleMembershipGrant(RoleEventsMixin):
    name = 'manager.role.membership.grant'
    label = _('role membership grant')

    @classmethod
    def record(cls, *, user, session, role, member, api=False):
        data = {'member_name': str(member)}
        return super().record(user=user, session=session, role=role, references=[member], data=data, api=api)

    @classmethod
    def get_message(cls, event, context):
        role, member = event.get_typed_references(Role, (DeletedUser, User))
        role = role or event.get_data('role_name')
        member = member or event.get_data('member_name')
        if context == member:
            return _('membership grant in role "%s"') % role
        elif context == role:
            return _('membership grant to user "%s"') % member
        else:
            return _('membership grant to user "{member}" in role "{role}"').format(member=member, role=role)


class ManagerRoleMembershipRemoval(RoleEventsMixin):
    name = 'manager.role.membership.removal'
    label = _('role membership removal')

    @classmethod
    def record(cls, *, user, session, role, member, api=False):
        data = {'member_name': str(member)}
        return super().record(user=user, session=session, role=role, references=[member], data=data, api=api)

    @classmethod
    def get_message(cls, event, context):
        role, member = event.get_typed_references(Role, (DeletedUser, User))
        role = role or event.get_data('role_name')
        member = member or event.get_data('member_name')
        if context == member:
            return _('membership removal from role "%s"') % role
        elif context == role:
            return _('membership removal of user "%s"') % member
        else:
            return _('membership removal of user "{member}" from role "{role}"').format(
                member=member, role=role
            )


class ManagerRoleInheritanceAddition(RoleEventsMixin):
    name = 'manager.role.inheritance.addition'
    label = _('role inheritance addition')

    @classmethod
    def record(cls, *, user, session, parent, child):
        data = {
            'child_name': str(child),
            'child_uuid': child.uuid,
        }
        return super().record(user=user, session=session, role=parent, references=[child], data=data)

    @classmethod
    def get_message(cls, event, context):
        parent, child = event.get_typed_references(Role, Role)
        parent = parent or event.get_data('role_name')
        child = child or event.get_data('child_name')
        if context == child:
            return _('inheritance addition from parent role "%s"') % parent
        elif context == parent:
            return _('inheritance addition to child role "%s"') % child
        else:
            return _('inheritance addition from parent role "{parent}" to child role "{child}"').format(
                parent=parent, child=child
            )


class ManagerRoleInheritanceRemoval(ManagerRoleInheritanceAddition):
    name = 'manager.role.inheritance.removal'
    label = _('role inheritance removal')

    @classmethod
    def get_message(cls, event, context):
        parent, child = event.get_typed_references(Role, Role)
        parent = parent or event.get_data('role_name')
        child = child or event.get_data('child_name')
        if context == child:
            return _('inheritance removal from parent role "%s"') % parent
        elif context == parent:
            return _('inheritance removal to child role "%s"') % child
        else:
            return _('inheritance removal from parent role "{parent}" to child role "{child}"').format(
                parent=parent, child=child
            )


class ManagerRoleAdministratorRoleAddition(RoleEventsMixin):
    name = 'manager.role.administrator.role.addition'
    label = _('role administrator role addition')

    @classmethod
    def record(cls, *, user, session, role, admin_role):
        data = {
            'admin_role_name': str(admin_role),
            'admin_role_uuid': admin_role.uuid,
        }
        return super().record(user=user, session=session, role=role, references=[admin_role], data=data)

    @classmethod
    def get_message(cls, event, context):
        role, admin_role = event.get_typed_references(Role, Role)
        role = role or event.get_data('role_name')
        admin_role = admin_role or event.get_data('admin_role_name')
        if context == role:
            return _('addition of role "%s" as administrator') % admin_role
        elif context == admin_role:
            return _('addition as administrator of role "%s"') % role
        else:
            return _('addition of role "{admin_role}" as administrator of role "{role}"').format(
                admin_role=admin_role, role=role
            )


class ManagerRoleAdministratorRoleRemoval(ManagerRoleAdministratorRoleAddition):
    name = 'manager.role.administrator.role.removal'
    label = _('role administrator role removal')

    @classmethod
    def get_message(cls, event, context):
        role, admin_role = event.get_typed_references(Role, Role)
        role = role or event.get_data('role_name')
        admin_role = admin_role or event.get_data('admin_role_name')
        if context == role:
            return _('removal of role "%s" as administrator') % admin_role
        elif context == admin_role:
            return _('removal as administrator of role "%s"') % role
        else:
            return _('removal of role "{admin_role}" as administrator of role "{role}"').format(
                admin_role=admin_role, role=role
            )


class ManagerRoleAdministratorUserAddition(RoleEventsMixin):
    name = 'manager.role.administrator.user.addition'
    label = _('role administrator user addition')

    @classmethod
    def record(cls, *, user, session, role, admin_user):
        data = {
            'admin_user_name': str(admin_user),
            'admin_user_uuid': admin_user.uuid,
        }
        return super().record(user=user, session=session, role=role, references=[admin_user], data=data)

    @classmethod
    def get_message(cls, event, context):
        role, admin_user = event.get_typed_references(Role, (DeletedUser, User))
        role = role or event.get_data('role_name')
        admin_user = admin_user or event.get_data('admin_user_name')
        if context == role:
            return _('addition of user "%s" as administrator') % admin_user
        elif context == admin_user:
            return _('addition as administrator of role "%s"') % role
        else:
            return _('addition of user "{admin_user}" as administrator of role "{role}"').format(
                admin_user=admin_user, role=role
            )


class ManagerRoleAdministratorUserRemoval(ManagerRoleAdministratorUserAddition):
    name = 'manager.role.administrator.user.removal'
    label = _('role administrator user removal')

    @classmethod
    def get_message(cls, event, context):
        role, admin_user = event.get_typed_references(Role, (DeletedUser, User))
        role = role or event.get_data('role_name')
        admin_user = admin_user or event.get_data('admin_user_name')
        if context == role:
            return _('removal of user "%s" as administrator') % admin_user
        elif context == admin_user:
            return _('removal as administrator of role "%s"') % role
        else:
            return _('removal of user "{admin_user}" as administrator of role "{role}"').format(
                admin_user=admin_user, role=role
            )


class ServiceEventsMixin(EventTypeWithService):
    @classmethod
    def record(cls, *, user, service, session=None, references=None, data=None, api=False):
        references = references or []
        references = [service] + references
        data = data or {}
        try:
            service = service.oidcclient
        except ObjectDoesNotExist:
            pass
        data.update({'kind': service.__class__.__name__})
        return super().record(
            service=service, user=user, session=session, references=references, data=data, api=api
        )


class ManagerServiceCreation(ServiceEventsMixin):
    name = 'manager.service.creation'
    label = _('service creation')

    @classmethod
    def get_message(cls, event, context):
        (service,) = event.get_typed_references(Service)
        name = service.name if service else None
        name = name if name else event.get_data('service_name')
        kind = event.get_data('kind')
        return _('creation of %(service_kind)s "%(service_name)s"') % {
            'service_kind': kind,
            'service_name': name,
        }


class ManagerServiceDeletion(ServiceEventsMixin):
    name = 'manager.service.deletion'
    label = _('service creation')

    @classmethod
    def get_message(cls, event, context):
        (service,) = event.get_typed_references(Service)
        name = service.name if service else None
        name = name if name else event.get_data('service_name')
        kind = event.get_data('kind')
        return _('deletion of %(service_kind)s "%(service_name)s"') % {
            'service_kind': kind,
            'service_name': name,
        }


class ManagerServiceEdit(ServiceEventsMixin):
    name = 'manager.service.edit'
    label = _('service configuration edition')

    @classmethod
    def record(
        cls,
        *,
        user,
        service,
        old_value=None,
        new_value=None,
        conf_name=None,
        session=None,
        references=None,
        data=None,
        api=False,
    ):
        data = data or {}
        if isinstance(old_value, File):
            if old_value.name:
                old_value = '%s (%s)' % (
                    os.path.basename(old_value.name),
                    hashlib.sha256(old_value.read()).hexdigest(),
                )
            else:
                old_value = None
            if new_value is False:
                new_value = None

        if isinstance(new_value, File):
            if new_value.name:
                new_value = '%s (%s)' % (new_value.name, hashlib.sha256(new_value.read()).hexdigest())
            else:
                new_value = None

        data.update(
            {
                'old': old_value,
                'new': new_value,
                'conf_name': conf_name,
            }
        )
        return super().record(
            user=user, service=service, session=session, references=references, data=data, api=api
        )

    @classmethod
    def get_message(cls, event, context):
        (service,) = event.get_typed_references(Service)
        name = service.name if service else None
        name = name if name else event.get_data('service_name')
        kind = event.get_data('kind')
        conf_name = event.get_data('conf_name')
        old = event.get_data('old')
        new = event.get_data('new')

        if old is None:
            msg = _('%(service_kind)s "%(service_name)s" : adding %(conf_name)s with value "%(new_value)s"')
        elif new is None:
            msg = _('%(service_kind)s "%(service_name)s" : removing %(conf_name)s with value "%(old_value)s"')
        else:
            msg = _(
                '%(service_kind)s "%(service_name)s" : changing %(conf_name)s from "%(old_value)s" to "%(new_value)s"'
            )
        return msg % {
            'conf_name': conf_name,
            'new_value': new,
            'old_value': old,
            'service_kind': kind,
            'service_name': name,
        }


class ServiceRoleMixin(ServiceEventsMixin):
    @classmethod
    def record(cls, *, user, service, role, session=None, references=None, data=None, api=False):
        data = data or {}
        data.update({'role_name': role.name, 'role_pk': role.pk, 'role_slug': role.slug})
        return super().record(
            user=user, service=service, session=session, references=references, data=data, api=api
        )


class ManagerServiceRoleAdd(ServiceRoleMixin):
    name = 'manager.service.role.add'
    label = _('service add role')

    @classmethod
    def get_message(cls, event, context):
        (service,) = event.get_typed_references(Service)
        name = service.name if service else None
        name = name if name else event.get_data('service_name')
        kind = event.get_data('kind')
        role = {k: event.get_data('role_%s' % k) for k in ('name', 'pk', 'slug')}
        return _('%(service_kind)s "%(service_name)s" : add role "%(role_name)s" (%(role_slug)s)') % {
            'role_name': role['name'],
            'role_slug': role['slug'],
            'service_kind': kind,
            'service_name': name,
        }


class ManagerServiceRoleDelete(ServiceRoleMixin):
    name = 'manager.service.role.delete'
    label = _('service delete role')

    @classmethod
    def get_message(cls, event, context):
        (service,) = event.get_typed_references(Service)
        name = service.name if service else None
        name = name if name else event.get_data('service_name')
        kind = event.get_data('kind')
        role = {k: event.get_data('role_%s' % k) for k in ('name', 'pk', 'slug')}
        return _('%(service_kind)s "%(service_name)s" : delete role "%(role_name)s" (%(role_slug)s)') % {
            'role_name': role['name'],
            'role_slug': role['slug'],
            'service_kind': kind,
            'service_name': name,
        }


class UserCsvImportAction(EventTypeDefinition):
    name = 'manager.user.csvimport.run'
    label = _('CSV import')

    @classmethod
    def record(
        cls,
        *,
        import_uuid,
        report_uuid,
        action_name,
        user=None,
        session=None,
        data=None,
        references=None,
        api=False,
    ):
        data = data if data is not None else {}
        data.update({'import_uuid': import_uuid, 'report_uuid': report_uuid, 'action_name': action_name})
        references = references or []
        references.append(user)
        return super().record(user=user, session=session, data=data, references=references, api=api)

    @classmethod
    def get_message(cls, event, context):
        action_name = event.get_data('action_name')
        return cls.format_message(event, action_name)

    @classmethod
    def format_message(cls, event, fmt, **kwargs):
        import_uuid = event.get_data('import_uuid')
        report_uuid = event.get_data('report_uuid')
        fmt = '<a href="{_report_url}">{_csv_import}</a> ' + fmt
        kwargs.update(
            {
                '_report_url': reverse(
                    'a2-manager-users-import-report',
                    kwargs={'import_uuid': import_uuid, 'report_uuid': report_uuid},
                ),
                '_csv_import': _('CSV user import %(uuid)s') % {'uuid': report_uuid},
            }
        )
        return format_html(fmt, **kwargs)


class UserCsvImportUserAction(UserCsvImportAction):
    name = 'manager.user.csvimport.action'
    label = _('CSV import user action')

    @classmethod
    def record(
        cls,
        *,
        import_uuid,
        report_uuid,
        action_name,
        user_uuid,
        fieldname=None,
        value=None,
        user=None,
        session=None,
        data=None,
        api=False,
    ):
        data = data if data else {}
        try:  # ensure given value is json serializable
            json.dumps(value)
        except TypeError:
            if isinstance(value, (datetime.date, datetime.datetime)):
                value = value.isoformat()
            else:
                value = str(value)
        data.update({'user_uuid': user_uuid, 'fieldname': fieldname, 'value': value})
        return super().record(
            import_uuid=import_uuid,
            report_uuid=report_uuid,
            action_name=action_name,
            user=user,
            session=session,
            data=data,
            api=api,
        )

    @classmethod
    def get_message(cls, event, context):
        user = cls.get_user(event)
        action_name = event.get_data('action_name')
        fieldname = event.get_data('fieldname')
        value = event.get_data('value')
        if fieldname:
            if isinstance(value, str):
                value = '"%s"' % value
            elif value is None:
                value = 'none'

            return cls.format_message(
                event,
                _('user {user!s} {action_name} {fieldname} : {value}'),
                user=user,
                action_name=action_name,
                fieldname=fieldname,
                value=value,
            )
        return cls.format_message(event, _('user {user!s} {action_name}'), action_name=action_name, user=user)

    @classmethod
    def get_user(cls, event):
        user_uuid = event.get_data('user_uuid')
        try:
            return User.objects.get(uuid=user_uuid)
        except User.DoesNotExist:
            pass
        try:
            return DeletedUser.objects.get(old_uuid=user_uuid)
        except User.DoesNotExist:
            pass
        return None
