# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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

from django.utils.translation import gettext_lazy as _

from authentic2.apps.journal.models import EventTypeDefinition
from authentic2.apps.journal.utils import form_to_old_new

from .models import BaseAuthenticator


class AuthenticatorEvents(EventTypeDefinition):
    @classmethod
    def record(cls, *, user, session, authenticator, data=None):
        data = data or {}
        data.update({'authenticator_name': str(authenticator), 'authenticator_uuid': str(authenticator.uuid)})
        return super().record(user=user, session=session, references=[authenticator], data=data)


class AuthenticatorCreation(AuthenticatorEvents):
    name = 'authenticator.creation'
    label = _('authenticator creation')

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        if context != authenticator:
            return _('creation of authenticator "%s"') % authenticator
        else:
            return _('creation')


class AuthenticatorEdit(AuthenticatorEvents):
    name = 'authenticator.edit'
    label = _('authenticator edit')

    @classmethod
    def record(cls, *, user, session, forms):
        data = form_to_old_new(forms[0])
        for form in forms[1:]:
            old_new = form_to_old_new(form)
            data['old'].update(old_new['old'])
            data['new'].update(old_new['new'])
        return super().record(user=user, session=session, authenticator=forms[0].instance, data=data)

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        new = event.get_data('new') or {}
        edited_attributes = ', '.join(new) or ''
        if context != authenticator:
            return _('edit of authenticator "{authenticator}" ({change})').format(
                authenticator=authenticator, change=edited_attributes
            )
        else:
            return _('edit ({change})').format(change=edited_attributes)


class AuthenticatorEnable(AuthenticatorEvents):
    name = 'authenticator.enable'
    label = _('authenticator enable')

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        if context != authenticator:
            return _('enable of authenticator "%s"') % authenticator
        else:
            return _('enable')


class AuthenticatorDisable(AuthenticatorEvents):
    name = 'authenticator.disable'
    label = _('authenticator disable')

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        if context != authenticator:
            return _('disable of authenticator "%s"') % authenticator
        else:
            return _('disable')


class AuthenticatorDeletion(AuthenticatorEvents):
    name = 'authenticator.deletion'
    label = _('authenticator deletion')

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        return _('deletion of authenticator "%s"') % authenticator


class AuthenticatorRelatedObjectEvents(AuthenticatorEvents):
    @classmethod
    def record(cls, *, user, session, related_object, data=None):
        data = data or {}
        data.update({'related_object': related_object.get_journal_text()})
        return super().record(
            user=user, session=session, authenticator=related_object.authenticator, data=data
        )


class AuthenticatorRelatedObjectCreation(AuthenticatorRelatedObjectEvents):
    name = 'authenticator.related_object.creation'
    label = _('Authenticator related object creation')

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        related_object = event.get_data('related_object')
        if context != authenticator:
            return _('creation of object "{related_object}" in authenticator "{authenticator}"').format(
                related_object=related_object, authenticator=authenticator
            )
        else:
            return _('creation of object "%s"') % related_object


class AuthenticatorRelatedObjectEdit(AuthenticatorRelatedObjectEvents):
    name = 'authenticator.related_object.edit'
    label = _('Authenticator related object edit')

    @classmethod
    def record(cls, *, user, session, form):
        return super().record(
            user=user,
            session=session,
            related_object=form.instance,
            data=form_to_old_new(form),
        )

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        related_object = event.get_data('related_object')
        new = event.get_data('new') or {}
        edited_attributes = ', '.join(new) or ''
        if context != authenticator:
            return _(
                'edit of object "{related_object}" in authenticator "{authenticator}" ({change})'
            ).format(
                related_object=related_object,
                authenticator=authenticator,
                change=edited_attributes,
            )
        else:
            return _('edit of object "{related_object}" ({change})').format(
                related_object=related_object, change=edited_attributes
            )


class AuthenticatorRelatedObjectDeletion(AuthenticatorRelatedObjectEvents):
    name = 'authenticator.related_object.deletion'
    label = _('Authenticator related object deletion')

    @classmethod
    def get_message(cls, event, context):
        (authenticator,) = event.get_typed_references(BaseAuthenticator)
        authenticator = authenticator or event.get_data('authenticator_name')
        related_object = event.get_data('related_object')
        if context != authenticator:
            return _('deletion of object "{related_object}" in authenticator "{authenticator}"').format(
                related_object=related_object, authenticator=authenticator
            )
        else:
            return _('deletion of object "%s"') % related_object
