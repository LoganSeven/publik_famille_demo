from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import Role
from authentic2.apps.journal.models import EventTypeDefinition
from authentic2.custom_user.models import DeletedUser
from authentic2.manager.journal_event_types import RoleEventsMixin

User = get_user_model()


class OIDCUserErrorEvent(EventTypeDefinition):
    name = 'auth.oidc.user_error'
    label = _('OIDC user error')

    @classmethod
    def record(cls, *, user, session, sub, issuer, data=None):
        data = data or {}
        data.update({'sub': sub, 'issuer': issuer})
        return super().record(user=user, session=session, data=data)

    @classmethod
    def get_message(cls, event, context):
        sub = event.get_data('sub')
        issuer = event.get_data('issuer')
        fmt = _('Cannot create user for sub "%(sub)s" as issuer "%(issuer)s" does not allow it')
        return fmt % {'sub': sub, 'issuer': issuer}


class OIDCBackendErrorEvent(EventTypeDefinition):
    name = 'auth.oidc.claim_error'
    label = _('OIDC authentication error')

    @classmethod
    def record(cls, *, user, session, claim, source_name, missing=True, data=None):
        data = data or {}
        data.update({'claim': claim, 'source': source_name, 'missing': missing})
        return super().record(user=user, session=session, data=data)

    @classmethod
    def get_message(cls, event, context):
        if event.get_data('missing'):
            msg = _('Missconfigured account, missing required claim %(claim)s in %(source)s')
        else:
            msg = _('Missconfigured account, invalid value for required claim %(claim)s in %(source)s')
        return msg % {'claim': event.get_data('claim'), 'source': event.get_data('source')}


class OIDCBackendAddRoleAction(RoleEventsMixin):
    name = 'auth.oidc.add_role_action'
    label = _('OIDC role edition')

    @classmethod
    def record(cls, *, user, session, role, condition=False, adding=True, data=None):
        data = data or {}
        data.update({'adding': bool(adding), 'condition': condition})
        return super().record(user=user, session=session, role=role, references=[user], data=data)

    @classmethod
    def get_message(cls, event, context):
        (role, user) = event.get_typed_references(Role, (DeletedUser, User))
        role = role or event.get_data('role_name')
        adding = event.get_data('adding', False)
        condition = event.get_data('condition', '')
        if condition:
            condition_reason = _(' based on condition : %(condition)s') % {'condition': condition}
        else:
            condition_reason = ''
        if adding:
            fmt = _('adding role "%(role)s" to user "%(user)s"%(condition_reason)s')
        else:
            fmt = _('removing role "%(role)s" to user "%(user)s"%(condition_reason)s')
        return fmt % {'role': role, 'user': user, 'condition_reason': condition_reason}
