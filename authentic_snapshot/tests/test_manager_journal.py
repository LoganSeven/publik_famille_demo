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
from unittest import mock

import pytest
from django.contrib.sessions.models import Session
from django.utils.timezone import make_aware

from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.apps.journal.models import (
    Event,
    EventType,
    EventTypeDefinition,
    _registry,
    clean_registry,
    event_type_cache,
)
from authentic2.custom_user.models import DeletedUser, Profile, ProfileType, User
from authentic2.journal import journal
from authentic2.models import Service
from authentic2.utils.crypto import new_base64url_id
from authentic2_auth_oidc.models import OIDCProvider
from authentic2_auth_saml.models import SAMLAuthenticator, SetAttributeAction

from .utils import login, logout, text_content

SOME_UUID = new_base64url_id()
ANOTHER_UUID = new_base64url_id()


def test_journal_authorization(app, db, simple_user, admin):
    response = login(app, simple_user)
    app.get('/manage/journal/', status=403)

    logout(app)
    response = login(app, admin, path='/manage/')
    assert 'Global journal' in response
    app.get('/manage/journal/', status=200)


@pytest.fixture(autouse=True)
def events(db, superuser, freezer):
    Event.objects.all().delete()
    session1 = Session(session_key='1234')
    session2 = Session(session_key='abcd')

    ou = get_default_ou()
    user = User.objects.create(
        username='user', email='user@example.com', ou=ou, uuid='1' * 32, first_name='Johnny', last_name='doe'
    )
    profile_type = ProfileType.objects.create(name='One Type', slug='one-type')
    profile = Profile.objects.create(user=user, profile_type=profile_type, identifier='aaa')
    agent = User.objects.create(username='agent', email='agent@example.com', ou=ou, uuid='2' * 32)
    role_user = Role.objects.create(name='role1', ou=ou)
    role_agent = Role.objects.create(name='role2', ou=ou)
    service = Service.objects.create(name='service')
    authenticator = LoginPasswordAuthenticator.objects.create(slug='test')
    saml_authenticator = SAMLAuthenticator.objects.create(slug='saml')
    oidc_provider = OIDCProvider.objects.create(slug='oidc', name='OIDC')
    set_attribute_action = SetAttributeAction.objects.create(authenticator=saml_authenticator)

    deleted_user = User.objects.create(username='deleted', email='deleted@example.com', ou=ou, uuid='3' * 32)

    class EventFactory:
        date = make_aware(datetime.datetime(2020, 1, 1))

        def __call__(self, name, **kwargs):
            freezer.move_to(self.date)
            journal.record(name, **kwargs)
            assert Event.objects.latest('timestamp').type.name == name
            self.date += datetime.timedelta(hours=1)

    make = EventFactory()
    make('user.registration.request', email=user.email)
    make(
        'user.registration',
        user=user,
        session=session1,
        service=service,
        how='france-connect',
    )
    make('user.logout', user=user, session=session1)

    make(
        'manager.service.creation',
        user=user,
        session=session2,
        service=service,
    )
    make(
        'manager.service.deletion',
        user=user,
        session=session2,
        service=service,
    )
    make(
        'manager.service.edit',
        user=user,
        session=session2,
        service=service,
        old_value='foo',
        new_value='bar',
        conf_name='toto',
    )
    make(
        'manager.service.role.add',
        user=user,
        session=session2,
        service=service,
        role=role_user,
    )
    make(
        'manager.service.role.delete',
        user=user,
        session=session2,
        service=service,
        role=role_agent,
    )

    make('user.login.failure', service=service, authenticator=saml_authenticator, username='user')
    make('user.login.failure', authenticator=saml_authenticator, username='agent')
    make('user.login', user=user, session=session1, how='password')
    make('user.password.change', user=user, session=session1)
    edit_profile_form = mock.Mock(spec=['instance', 'initial', 'changed_data', 'cleaned_data'])
    edit_profile_form.initial = {'email': 'user@example.com', 'first_name': 'John'}
    edit_profile_form.changed_data = ['first_name']
    edit_profile_form.cleaned_data = {'first_name': 'Jane'}
    make('user.profile.edit', user=user, session=session1, form=edit_profile_form)
    make('user.service.sso.authorization', user=user, session=session1, service=service)
    make('user.service.sso', user=user, session=session1, service=service, how='password')
    make('user.service.sso.unauthorization', user=user, session=session1, service=service)
    make('user.deletion', user=user, session=session1, service=service)

    make('user.password.reset.request', email='USER@example.com', user=user)
    make('user.password.reset.failure', email='USER@example.com')
    make('user.password.reset', user=user)

    make('user.login', user=agent, session=session2, how='saml')

    create_form = mock.Mock(spec=['instance'])
    create_form.instance = user
    make('manager.user.creation', user=agent, session=session2, form=create_form)

    edit_form = mock.Mock(spec=['instance', 'initial', 'changed_data', 'cleaned_data'])
    edit_form.instance = user
    edit_form.initial = {'email': 'user@example.com', 'first_name': 'John'}
    edit_form.changed_data = ['first_name']
    edit_form.cleaned_data = {'first_name': 'Jane'}
    make('manager.user.profile.edit', user=agent, session=session2, form=edit_form)

    change_email_form = mock.Mock(spec=['instance', 'cleaned_data'])
    change_email_form.instance = user
    change_email_form.cleaned_data = {'new_email': 'jane@example.com'}
    make(
        'manager.user.email.change.request',
        user=agent,
        session=session2,
        form=change_email_form,
    )

    password_change_form = mock.Mock(spec=['instance', 'cleaned_data'])
    password_change_form.instance = user
    password_change_form.cleaned_data = {'generate_password': False, 'send_mail': False}
    make(
        'manager.user.password.change',
        user=agent,
        session=session2,
        form=password_change_form,
    )

    password_change_form.cleaned_data['send_mail'] = True
    make(
        'manager.user.password.change',
        user=agent,
        session=session2,
        form=password_change_form,
    )

    make(
        'manager.user.password.reset.request',
        user=agent,
        session=session2,
        target_user=user,
    )

    make(
        'manager.user.password.change.force',
        user=agent,
        session=session2,
        target_user=user,
    )
    make(
        'manager.user.password.change.unforce',
        user=agent,
        session=session2,
        target_user=user,
    )

    make('manager.user.activation', user=agent, session=session2, target_user=user)
    make('manager.user.deactivation', user=agent, session=session2, target_user=user)
    make('manager.user.deletion', user=agent, session=session2, target_user=user)
    make(
        'manager.user.sso.authorization.deletion',
        user=agent,
        session=session2,
        service=service,
        target_user=user,
    )

    make('manager.role.creation', user=agent, session=session2, role=role_user)
    role_edit_form = mock.Mock(spec=['instance', 'initial', 'changed_data', 'cleaned_data'])
    role_edit_form.instance = role_user
    role_edit_form.initial = {'name': role_user.name}
    role_edit_form.changed_data = ['name']
    role_edit_form.cleaned_data = {'name': 'changed role name'}
    make(
        'manager.role.edit',
        user=agent,
        session=session2,
        role=role_user,
        form=role_edit_form,
    )
    make('manager.role.deletion', user=agent, session=session2, role=role_user)
    make(
        'manager.role.membership.grant',
        user=agent,
        session=session2,
        role=role_user,
        member=user,
    )
    make(
        'manager.role.membership.removal',
        user=agent,
        session=session2,
        role=role_user,
        member=user,
    )

    make(
        'manager.role.inheritance.addition',
        user=agent,
        session=session2,
        parent=role_agent,
        child=role_user,
    )
    make(
        'manager.role.inheritance.removal',
        user=agent,
        session=session2,
        parent=role_agent,
        child=role_user,
    )

    make(
        'manager.role.administrator.role.addition',
        user=agent,
        session=session2,
        role=role_user,
        admin_role=role_agent,
    )
    make(
        'manager.role.administrator.role.removal',
        user=agent,
        session=session2,
        role=role_user,
        admin_role=role_agent,
    )

    make(
        'manager.role.administrator.user.addition',
        user=agent,
        session=session2,
        role=role_user,
        admin_user=user,
    )
    make(
        'manager.role.administrator.user.removal',
        user=agent,
        session=session2,
        role=role_user,
        admin_user=user,
    )
    make(
        'user.phone.change.request',
        user=user,
        old_phone='+33122334455',
        session=session1,
        new_phone='+33111223344',
    )
    make(
        'user.phone.change',
        user=user,
        session=session1,
        old_phone='+33122334455',
        new_phone='+33111223344',
    )
    make(
        'user.email.change.request',
        user=user,
        session=session1,
        new_email='new@example.com',
    )
    make(
        'user.email.change',
        user=user,
        session=session1,
        old_email='old@example.com',
        new_email='new@example.com',
    )
    make(
        'manager.user.deactivation',
        target_user=user,
        reason='ldap-not-present',
    )
    make(
        'manager.user.deactivation',
        target_user=user,
        reason='ldap-old-source',
    )
    make(
        'manager.user.activation',
        target_user=user,
        reason='ldap-reactivation',
    )

    make('user.service.sso.refusal', user=user, session=session1, service=service)
    make('user.service.sso.denial', user=user, session=session1, service=service)

    make(
        'user.profile.add',
        user=agent,
        profile=profile,
    )
    make(
        'user.profile.update',
        user=agent,
        profile=profile,
    )
    make(
        'user.profile.delete',
        user=agent,
        profile=profile,
    )
    make('user.notification.inactivity', user=user, days_of_inactivity=120, days_to_deletion=20)
    make('user.deletion.inactivity', user=user, days_of_inactivity=140)
    make('authenticator.creation', user=agent, session=session2, authenticator=authenticator)
    authenticator_edit_form = mock.Mock(spec=['instance', 'initial', 'changed_data', 'cleaned_data'])
    authenticator_edit_form.instance = authenticator
    authenticator_edit_form.initial = {'name': 'old'}
    authenticator_edit_form.changed_data = ['name']
    authenticator_edit_form.cleaned_data = {'name': 'new'}
    make('authenticator.edit', user=agent, session=session2, forms=[authenticator_edit_form])
    make('authenticator.enable', user=agent, session=session2, authenticator=authenticator)
    make('authenticator.disable', user=agent, session=session2, authenticator=authenticator)
    make('authenticator.deletion', user=agent, session=session2, authenticator=authenticator)
    make(
        'authenticator.related_object.creation',
        user=agent,
        session=session2,
        related_object=set_attribute_action,
    )
    action_edit_form = mock.Mock(spec=['instance', 'initial', 'changed_data', 'cleaned_data'])
    action_edit_form.instance = set_attribute_action
    action_edit_form.initial = {'from_name': 'old'}
    action_edit_form.changed_data = ['from_name']
    action_edit_form.cleaned_data = {'from_name': 'new'}
    make('authenticator.related_object.edit', user=agent, session=session2, form=action_edit_form)
    make(
        'authenticator.related_object.deletion',
        user=agent,
        session=session2,
        related_object=set_attribute_action,
    )
    make('user.notification.activity', actor=service, target_user=user)
    make('user.notification.activity', actor=superuser, target_user=user)

    make('user.password.reset', user=deleted_user)
    deleted_user.delete()

    make(
        'provider.keyset.change',
        provider=oidc_provider.name,
        new_keyset={'b', 'c', 'd', 'e'},
        old_keyset={'a', 'b', 'c'},
    )
    make(
        'user.su_token_generation',
        user=superuser,
        session=session2,
        as_username=user.username,
        as_userid=user.id,
    )
    make(
        'manager.user.csvimport.run',
        import_uuid=SOME_UUID,
        report_uuid=ANOTHER_UUID,
        action_name='start import',
    )
    make(
        'manager.user.csvimport.action',
        import_uuid=SOME_UUID,
        report_uuid=ANOTHER_UUID,
        user_uuid=agent.uuid,
        action_name='create',
    )
    make(
        'manager.user.csvimport.action',
        import_uuid=SOME_UUID,
        report_uuid=ANOTHER_UUID,
        user_uuid=agent.uuid,
        action_name='update property',
        fieldname='email',
        value='agent@example.com',
    )

    make(
        'manager.user.csvimport.run',
        import_uuid=SOME_UUID,
        report_uuid=ANOTHER_UUID,
        action_name='end import',
    )
    make(
        'auth.oidc.claim_error',
        claim='given_name',
        source_name='id_token',
        missing=True,
    )
    make(
        'auth.oidc.add_role_action',
        user=user,
        role=role_user,
        adding=True,
    )
    make('auth.oidc.user_error', sub='something@example.com', issuer='http://example.com/')

    # verify we created at least one event for each type
    assert set(Event.objects.values_list('type__name', flat=True)) == set(_registry)

    return locals()


def extract_journal(response):
    rows = []
    seen_event_ids = set()
    while True:
        for tr in response.pyquery('tr[data-event-type]'):
            # page can overlap when they contain less than 20 items (to prevent orphan rows)
            event_id = tr.attrib['data-event-id']
            if event_id not in seen_event_ids:
                rows.append(response.pyquery(tr))
                seen_event_ids.add(event_id)
        if 'Previous page' not in response:
            break
        response = response.click('Previous page', index=0)

    rows.reverse()
    content = [
        {
            'timestamp': text_content(row.find('.journal-list--timestamp-column')[0]).strip(),
            'type': row[0].attrib['data-event-type'],
            'user': text_content(row.find('.journal-list--user-column')[0]).strip(),
            'message': text_content(row.find('.journal-list--message-column')[0]),
        }
        for row in rows
    ]
    return content


def test_global_journal(app, superuser, events):
    response = login(app, user=superuser, path='/manage/')
    set_attribute_action = SetAttributeAction.objects.get()

    # remove event about admin login
    Event.objects.order_by('-id').filter(type__name='user.login', user=superuser)[0].delete()

    # get deleted user
    deleted_user = DeletedUser.objects.all().first()

    response = response.click('Global journal')

    content = extract_journal(response)

    assert content == [
        {
            'message': 'registration request with email "user@example.com"',
            'timestamp': 'Jan. 1, 2020, midnight',
            'type': 'user.registration.request',
            'user': '-',
        },
        {
            'message': 'registration using FranceConnect',
            'timestamp': 'Jan. 1, 2020, 1 a.m.',
            'type': 'user.registration',
            'user': 'Johnny doe',
        },
        {
            'message': 'logout',
            'timestamp': 'Jan. 1, 2020, 2 a.m.',
            'type': 'user.logout',
            'user': 'Johnny doe',
        },
        {
            'message': 'creation of Service "service"',
            'timestamp': 'Jan. 1, 2020, 3 a.m.',
            'type': 'manager.service.creation',
            'user': 'Johnny doe',
        },
        {
            'message': 'deletion of Service "service"',
            'timestamp': 'Jan. 1, 2020, 4 a.m.',
            'type': 'manager.service.deletion',
            'user': 'Johnny doe',
        },
        {
            'message': 'Service "service" : changing toto from "foo" to "bar"',
            'timestamp': 'Jan. 1, 2020, 5 a.m.',
            'type': 'manager.service.edit',
            'user': 'Johnny doe',
        },
        {
            'message': 'Service "service" : add role "role1" (role1)',
            'timestamp': 'Jan. 1, 2020, 6 a.m.',
            'type': 'manager.service.role.add',
            'user': 'Johnny doe',
        },
        {
            'message': 'Service "service" : delete role "role2" (role2)',
            'timestamp': 'Jan. 1, 2020, 7 a.m.',
            'type': 'manager.service.role.delete',
            'user': 'Johnny doe',
        },
        {
            'message': 'login failure with username "user" on authenticator SAML - saml',
            'timestamp': 'Jan. 1, 2020, 8 a.m.',
            'type': 'user.login.failure',
            'user': '-',
        },
        {
            'message': 'login failure with username "agent" on authenticator SAML - saml',
            'timestamp': 'Jan. 1, 2020, 9 a.m.',
            'type': 'user.login.failure',
            'user': '-',
        },
        {
            'message': 'login using password',
            'timestamp': 'Jan. 1, 2020, 10 a.m.',
            'type': 'user.login',
            'user': 'Johnny doe',
        },
        {
            'message': 'password change',
            'timestamp': 'Jan. 1, 2020, 11 a.m.',
            'type': 'user.password.change',
            'user': 'Johnny doe',
        },
        {
            'message': 'profile edit (first name)',
            'timestamp': 'Jan. 1, 2020, noon',
            'type': 'user.profile.edit',
            'user': 'Johnny doe',
        },
        {
            'message': 'authorization of single sign on with "service"',
            'timestamp': 'Jan. 1, 2020, 1 p.m.',
            'type': 'user.service.sso.authorization',
            'user': 'Johnny doe',
        },
        {
            'message': 'service single sign on with "service"',
            'timestamp': 'Jan. 1, 2020, 2 p.m.',
            'type': 'user.service.sso',
            'user': 'Johnny doe',
        },
        {
            'message': 'unauthorization of single sign on with "service"',
            'timestamp': 'Jan. 1, 2020, 3 p.m.',
            'type': 'user.service.sso.unauthorization',
            'user': 'Johnny doe',
        },
        {
            'message': 'user deletion',
            'timestamp': 'Jan. 1, 2020, 4 p.m.',
            'type': 'user.deletion',
            'user': 'Johnny doe',
        },
        {
            'message': 'password reset request with email "user@example.com"',
            'timestamp': 'Jan. 1, 2020, 5 p.m.',
            'type': 'user.password.reset.request',
            'user': 'Johnny doe',
        },
        {
            'message': 'password reset failure with email "USER@example.com"',
            'timestamp': 'Jan. 1, 2020, 6 p.m.',
            'type': 'user.password.reset.failure',
            'user': '-',
        },
        {
            'message': 'password reset',
            'timestamp': 'Jan. 1, 2020, 7 p.m.',
            'type': 'user.password.reset',
            'user': 'Johnny doe',
        },
        {
            'message': 'login using SAML',
            'timestamp': 'Jan. 1, 2020, 8 p.m.',
            'type': 'user.login',
            'user': 'agent',
        },
        {
            'message': 'creation of user "Johnny doe"',
            'timestamp': 'Jan. 1, 2020, 9 p.m.',
            'type': 'manager.user.creation',
            'user': 'agent',
        },
        {
            'message': 'edit of user "Johnny doe" (first name)',
            'timestamp': 'Jan. 1, 2020, 10 p.m.',
            'type': 'manager.user.profile.edit',
            'user': 'agent',
        },
        {
            'message': 'email change of user "Johnny doe" for email address "jane@example.com"',
            'timestamp': 'Jan. 1, 2020, 11 p.m.',
            'type': 'manager.user.email.change.request',
            'user': 'agent',
        },
        {
            'message': 'password change of user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, midnight',
            'type': 'manager.user.password.change',
            'user': 'agent',
        },
        {
            'message': 'password change of user "Johnny doe" and notification by mail',
            'timestamp': 'Jan. 2, 2020, 1 a.m.',
            'type': 'manager.user.password.change',
            'user': 'agent',
        },
        {
            'message': 'password reset request of "Johnny doe" sent to "user@example.com"',
            'timestamp': 'Jan. 2, 2020, 2 a.m.',
            'type': 'manager.user.password.reset.request',
            'user': 'agent',
        },
        {
            'message': 'mandatory password change at next login set for user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 3 a.m.',
            'type': 'manager.user.password.change.force',
            'user': 'agent',
        },
        {
            'message': 'mandatory password change at next login unset for user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 4 a.m.',
            'type': 'manager.user.password.change.unforce',
            'user': 'agent',
        },
        {
            'message': 'activation of user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 5 a.m.',
            'type': 'manager.user.activation',
            'user': 'agent',
        },
        {
            'message': 'deactivation of user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 6 a.m.',
            'type': 'manager.user.deactivation',
            'user': 'agent',
        },
        {
            'message': 'deletion of user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 7 a.m.',
            'type': 'manager.user.deletion',
            'user': 'agent',
        },
        {
            'message': 'deletion of authorization of single sign on with "service" of user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 8 a.m.',
            'type': 'manager.user.sso.authorization.deletion',
            'user': 'agent',
        },
        {
            'message': 'creation of role "role1"',
            'timestamp': 'Jan. 2, 2020, 9 a.m.',
            'type': 'manager.role.creation',
            'user': 'agent',
        },
        {
            'message': 'edit of role "role1" (name)',
            'timestamp': 'Jan. 2, 2020, 10 a.m.',
            'type': 'manager.role.edit',
            'user': 'agent',
        },
        {
            'message': 'deletion of role "role1"',
            'timestamp': 'Jan. 2, 2020, 11 a.m.',
            'type': 'manager.role.deletion',
            'user': 'agent',
        },
        {
            'message': 'membership grant to user "Johnny doe" in role "role1"',
            'timestamp': 'Jan. 2, 2020, noon',
            'type': 'manager.role.membership.grant',
            'user': 'agent',
        },
        {
            'message': 'membership removal of user "Johnny doe" from role "role1"',
            'timestamp': 'Jan. 2, 2020, 1 p.m.',
            'type': 'manager.role.membership.removal',
            'user': 'agent',
        },
        {
            'message': 'inheritance addition from parent role "role2" to child role "role1"',
            'timestamp': 'Jan. 2, 2020, 2 p.m.',
            'type': 'manager.role.inheritance.addition',
            'user': 'agent',
        },
        {
            'message': 'inheritance removal from parent role "role2" to child role "role1"',
            'timestamp': 'Jan. 2, 2020, 3 p.m.',
            'type': 'manager.role.inheritance.removal',
            'user': 'agent',
        },
        {
            'message': 'addition of role "role2" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 4 p.m.',
            'type': 'manager.role.administrator.role.addition',
            'user': 'agent',
        },
        {
            'message': 'removal of role "role2" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 5 p.m.',
            'type': 'manager.role.administrator.role.removal',
            'user': 'agent',
        },
        {
            'message': 'addition of user "Johnny doe" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 6 p.m.',
            'type': 'manager.role.administrator.user.addition',
            'user': 'agent',
        },
        {
            'message': 'removal of user "Johnny doe" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 7 p.m.',
            'type': 'manager.role.administrator.user.removal',
            'user': 'agent',
        },
        {
            'timestamp': 'Jan. 2, 2020, 8 p.m.',
            'type': 'user.phone.change.request',
            'user': 'Johnny doe',
            'message': 'phone change request to number "+33111223344"',
        },
        {
            'timestamp': 'Jan. 2, 2020, 9 p.m.',
            'type': 'user.phone.change',
            'user': 'Johnny doe',
            'message': 'phone number changed from "+33122334455" to "+33111223344"',
        },
        {
            'message': 'email change request for email address "new@example.com"',
            'timestamp': 'Jan. 2, 2020, 10 p.m.',
            'type': 'user.email.change.request',
            'user': 'Johnny doe',
        },
        {
            'message': 'email address changed from "old@example.com" to "new@example.com"',
            'timestamp': 'Jan. 2, 2020, 11 p.m.',
            'type': 'user.email.change',
            'user': 'Johnny doe',
        },
        {
            'timestamp': 'Jan. 3, 2020, midnight',
            'type': 'manager.user.deactivation',
            'user': '-',
            'message': (
                'automatic deactivation of user "Johnny doe" because the associated LDAP account does not'
                ' exist anymore'
            ),
        },
        {
            'timestamp': 'Jan. 3, 2020, 1 a.m.',
            'type': 'manager.user.deactivation',
            'user': '-',
            'message': (
                'automatic deactivation of user "Johnny doe" because the associated LDAP source has been'
                ' deleted'
            ),
        },
        {
            'message': (
                'automatic activation of user "Johnny doe" because the associated LDAP account reappeared'
            ),
            'timestamp': 'Jan. 3, 2020, 2 a.m.',
            'type': 'manager.user.activation',
            'user': '-',
        },
        {
            'message': 'refusal of single sign on with "service"',
            'timestamp': 'Jan. 3, 2020, 3 a.m.',
            'type': 'user.service.sso.refusal',
            'user': 'Johnny doe',
        },
        {
            'message': 'was denied single sign on with "service"',
            'timestamp': 'Jan. 3, 2020, 4 a.m.',
            'type': 'user.service.sso.denial',
            'user': 'Johnny doe',
        },
        {
            'timestamp': 'Jan. 3, 2020, 5 a.m.',
            'type': 'user.profile.add',
            'user': 'agent',
            'message': 'profile "aaa" of type "One Type" created for user "Johnny doe"',
        },
        {
            'timestamp': 'Jan. 3, 2020, 6 a.m.',
            'type': 'user.profile.update',
            'user': 'agent',
            'message': 'profile "aaa" of type "One Type" updated for user "Johnny doe"',
        },
        {
            'timestamp': 'Jan. 3, 2020, 7 a.m.',
            'type': 'user.profile.delete',
            'user': 'agent',
            'message': 'profile "aaa" of type "One Type" deleted for user "Johnny doe"',
        },
        {
            'message': 'notification sent to "user@example.com" after 120 days of '
            'inactivity. Account will be deleted in 20 days.',
            'timestamp': 'Jan. 3, 2020, 8 a.m.',
            'type': 'user.notification.inactivity',
            'user': 'Johnny doe',
        },
        {
            'message': 'user deletion after 140 days of inactivity, notification sent to '
            '"user@example.com".',
            'timestamp': 'Jan. 3, 2020, 9 a.m.',
            'type': 'user.deletion.inactivity',
            'user': 'Johnny doe',
        },
        {
            'message': 'creation of authenticator "Password"',
            'timestamp': 'Jan. 3, 2020, 10 a.m.',
            'type': 'authenticator.creation',
            'user': 'agent',
        },
        {
            'message': 'edit of authenticator "Password" (name)',
            'timestamp': 'Jan. 3, 2020, 11 a.m.',
            'type': 'authenticator.edit',
            'user': 'agent',
        },
        {
            'message': 'enable of authenticator "Password"',
            'timestamp': 'Jan. 3, 2020, noon',
            'type': 'authenticator.enable',
            'user': 'agent',
        },
        {
            'message': 'disable of authenticator "Password"',
            'timestamp': 'Jan. 3, 2020, 1 p.m.',
            'type': 'authenticator.disable',
            'user': 'agent',
        },
        {
            'message': 'deletion of authenticator "Password"',
            'timestamp': 'Jan. 3, 2020, 2 p.m.',
            'type': 'authenticator.deletion',
            'user': 'agent',
        },
        {
            'message': 'creation of object "Set an attribute (%s)" in authenticator "SAML - saml"'
            % set_attribute_action.pk,
            'timestamp': 'Jan. 3, 2020, 3 p.m.',
            'type': 'authenticator.related_object.creation',
            'user': 'agent',
        },
        {
            'message': 'edit of object "Set an attribute (%s)" in authenticator "SAML - saml" (from_name)'
            % set_attribute_action.pk,
            'timestamp': 'Jan. 3, 2020, 4 p.m.',
            'type': 'authenticator.related_object.edit',
            'user': 'agent',
        },
        {
            'message': 'deletion of object "Set an attribute (%s)" in authenticator "SAML - saml"'
            % set_attribute_action.pk,
            'timestamp': 'Jan. 3, 2020, 5 p.m.',
            'type': 'authenticator.related_object.deletion',
            'user': 'agent',
        },
        {
            'message': 'user "Johnny doe" activity notified by service "service"',
            'timestamp': 'Jan. 3, 2020, 6 p.m.',
            'type': 'user.notification.activity',
            'user': '-',
        },
        {
            'message': 'user "Johnny doe" activity notified by user "super user"',
            'timestamp': 'Jan. 3, 2020, 7 p.m.',
            'type': 'user.notification.activity',
            'user': 'super user',
        },
        {
            'message': 'password reset',
            'timestamp': 'Jan. 3, 2020, 8 p.m.',
            'type': 'user.password.reset',
            'user': f'deleted user (#{deleted_user.old_user_id}, deleted@example.com)',
        },
        {
            'message': 'Provider OIDC renewed its keyset with new keys [d, e] whereas old keys [a] are now deprecated',
            'timestamp': 'Jan. 3, 2020, 9 p.m.',
            'type': 'provider.keyset.change',
            'user': '-',
        },
        {
            'message': f'login as token generated for "user" (id={events["user"].id})',
            'timestamp': 'Jan. 3, 2020, 10 p.m.',
            'type': 'user.su_token_generation',
            'user': 'super user',
        },
        {
            'message': f'CSV user import {ANOTHER_UUID} start import',
            'timestamp': 'Jan. 3, 2020, 11 p.m.',
            'type': 'manager.user.csvimport.run',
            'user': '-',
        },
        {
            'message': f'CSV user import {ANOTHER_UUID} user agent create',
            'timestamp': 'Jan. 4, 2020, midnight',
            'type': 'manager.user.csvimport.action',
            'user': '-',
        },
        {
            'message': f'CSV user import {ANOTHER_UUID} user agent update property email : "agent@example.com"',
            'timestamp': 'Jan. 4, 2020, 1 a.m.',
            'type': 'manager.user.csvimport.action',
            'user': '-',
        },
        {
            'message': f'CSV user import {ANOTHER_UUID} end import',
            'timestamp': 'Jan. 4, 2020, 2 a.m.',
            'type': 'manager.user.csvimport.run',
            'user': '-',
        },
        {
            'message': 'Missconfigured account, missing required claim given_name in id_token',
            'timestamp': 'Jan. 4, 2020, 3 a.m.',
            'type': 'auth.oidc.claim_error',
            'user': '-',
        },
        {
            'message': 'adding role "role1" to user "Johnny doe"',
            'timestamp': 'Jan. 4, 2020, 4 a.m.',
            'type': 'auth.oidc.add_role_action',
            'user': 'Johnny doe',
        },
        {
            'message': 'Cannot create user for sub "something@example.com" as issuer "http://example.com/" does not allow it',
            'timestamp': 'Jan. 4, 2020, 5 a.m.',
            'type': 'auth.oidc.user_error',
            'user': '-',
        },
    ]

    agent_page = response.click('agent', index=1)
    assert 'agent' in agent_page.text

    response = response.click('Previous page', index=0)
    user_page = response.click('Johnny doe', index=1)
    assert 'Johnny doe' in user_page.text

    with pytest.raises(IndexError):
        response.click('deleted user')


def test_user_journal(app, superuser, events):
    response = login(app, user=superuser, path='/manage/')
    user = User.objects.get(username='user')

    response = app.get('/manage/users/%s/journal/' % user.id)
    content = extract_journal(response)

    assert content == [
        {
            'message': 'registration using FranceConnect',
            'timestamp': 'Jan. 1, 2020, 1 a.m.',
            'type': 'user.registration',
            'user': 'Johnny doe',
        },
        {
            'message': 'logout',
            'timestamp': 'Jan. 1, 2020, 2 a.m.',
            'type': 'user.logout',
            'user': 'Johnny doe',
        },
        {
            'message': 'creation of Service "service"',
            'timestamp': 'Jan. 1, 2020, 3 a.m.',
            'type': 'manager.service.creation',
            'user': 'Johnny doe',
        },
        {
            'message': 'deletion of Service "service"',
            'timestamp': 'Jan. 1, 2020, 4 a.m.',
            'type': 'manager.service.deletion',
            'user': 'Johnny doe',
        },
        {
            'message': 'Service "service" : changing toto from "foo" to "bar"',
            'timestamp': 'Jan. 1, 2020, 5 a.m.',
            'type': 'manager.service.edit',
            'user': 'Johnny doe',
        },
        {
            'message': 'Service "service" : add role "role1" (role1)',
            'timestamp': 'Jan. 1, 2020, 6 a.m.',
            'type': 'manager.service.role.add',
            'user': 'Johnny doe',
        },
        {
            'message': 'Service "service" : delete role "role2" (role2)',
            'timestamp': 'Jan. 1, 2020, 7 a.m.',
            'type': 'manager.service.role.delete',
            'user': 'Johnny doe',
        },
        {
            'message': 'login using password',
            'timestamp': 'Jan. 1, 2020, 10 a.m.',
            'type': 'user.login',
            'user': 'Johnny doe',
        },
        {
            'message': 'password change',
            'timestamp': 'Jan. 1, 2020, 11 a.m.',
            'type': 'user.password.change',
            'user': 'Johnny doe',
        },
        {
            'message': 'profile edit (first name)',
            'timestamp': 'Jan. 1, 2020, noon',
            'type': 'user.profile.edit',
            'user': 'Johnny doe',
        },
        {
            'message': 'authorization of single sign on with "service"',
            'timestamp': 'Jan. 1, 2020, 1 p.m.',
            'type': 'user.service.sso.authorization',
            'user': 'Johnny doe',
        },
        {
            'message': 'service single sign on with "service"',
            'timestamp': 'Jan. 1, 2020, 2 p.m.',
            'type': 'user.service.sso',
            'user': 'Johnny doe',
        },
        {
            'message': 'unauthorization of single sign on with "service"',
            'timestamp': 'Jan. 1, 2020, 3 p.m.',
            'type': 'user.service.sso.unauthorization',
            'user': 'Johnny doe',
        },
        {
            'message': 'user deletion',
            'timestamp': 'Jan. 1, 2020, 4 p.m.',
            'type': 'user.deletion',
            'user': 'Johnny doe',
        },
        {
            'message': 'password reset request with email "user@example.com"',
            'timestamp': 'Jan. 1, 2020, 5 p.m.',
            'type': 'user.password.reset.request',
            'user': 'Johnny doe',
        },
        {
            'message': 'password reset',
            'timestamp': 'Jan. 1, 2020, 7 p.m.',
            'type': 'user.password.reset',
            'user': 'Johnny doe',
        },
        {
            'message': 'creation by administrator',
            'timestamp': 'Jan. 1, 2020, 9 p.m.',
            'type': 'manager.user.creation',
            'user': 'agent',
        },
        {
            'message': 'edit by administrator (first name)',
            'timestamp': 'Jan. 1, 2020, 10 p.m.',
            'type': 'manager.user.profile.edit',
            'user': 'agent',
        },
        {
            'message': 'email change for email address "jane@example.com" requested by administrator',
            'timestamp': 'Jan. 1, 2020, 11 p.m.',
            'type': 'manager.user.email.change.request',
            'user': 'agent',
        },
        {
            'message': 'password change by administrator',
            'timestamp': 'Jan. 2, 2020, midnight',
            'type': 'manager.user.password.change',
            'user': 'agent',
        },
        {
            'message': 'password change by administrator and notification by mail',
            'timestamp': 'Jan. 2, 2020, 1 a.m.',
            'type': 'manager.user.password.change',
            'user': 'agent',
        },
        {
            'message': "password reset request by administrator sent to \"user@example.com\"",
            'timestamp': 'Jan. 2, 2020, 2 a.m.',
            'type': 'manager.user.password.reset.request',
            'user': 'agent',
        },
        {
            'message': 'mandatory password change at next login set by administrator',
            'timestamp': 'Jan. 2, 2020, 3 a.m.',
            'type': 'manager.user.password.change.force',
            'user': 'agent',
        },
        {
            'message': 'mandatory password change at next login unset by administrator',
            'timestamp': 'Jan. 2, 2020, 4 a.m.',
            'type': 'manager.user.password.change.unforce',
            'user': 'agent',
        },
        {
            'message': 'activation by administrator',
            'timestamp': 'Jan. 2, 2020, 5 a.m.',
            'type': 'manager.user.activation',
            'user': 'agent',
        },
        {
            'message': 'deactivation by administrator',
            'timestamp': 'Jan. 2, 2020, 6 a.m.',
            'type': 'manager.user.deactivation',
            'user': 'agent',
        },
        {
            'message': 'deletion by administrator',
            'timestamp': 'Jan. 2, 2020, 7 a.m.',
            'type': 'manager.user.deletion',
            'user': 'agent',
        },
        {
            'message': 'deletion of authorization of single sign on with "service" by administrator',
            'timestamp': 'Jan. 2, 2020, 8 a.m.',
            'type': 'manager.user.sso.authorization.deletion',
            'user': 'agent',
        },
        {
            'message': 'membership grant in role "role1"',
            'timestamp': 'Jan. 2, 2020, noon',
            'type': 'manager.role.membership.grant',
            'user': 'agent',
        },
        {
            'message': 'membership removal from role "role1"',
            'timestamp': 'Jan. 2, 2020, 1 p.m.',
            'type': 'manager.role.membership.removal',
            'user': 'agent',
        },
        {
            'message': 'addition as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 6 p.m.',
            'type': 'manager.role.administrator.user.addition',
            'user': 'agent',
        },
        {
            'message': 'removal as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 7 p.m.',
            'type': 'manager.role.administrator.user.removal',
            'user': 'agent',
        },
        {
            'timestamp': 'Jan. 2, 2020, 8 p.m.',
            'type': 'user.phone.change.request',
            'user': 'Johnny doe',
            'message': 'phone change request to number "+33111223344"',
        },
        {
            'timestamp': 'Jan. 2, 2020, 9 p.m.',
            'type': 'user.phone.change',
            'user': 'Johnny doe',
            'message': 'phone number changed from "+33122334455" to "+33111223344"',
        },
        {
            'message': 'email change request for email address "new@example.com"',
            'timestamp': 'Jan. 2, 2020, 10 p.m.',
            'type': 'user.email.change.request',
            'user': 'Johnny doe',
        },
        {
            'message': 'email address changed from "old@example.com" to "new@example.com"',
            'timestamp': 'Jan. 2, 2020, 11 p.m.',
            'type': 'user.email.change',
            'user': 'Johnny doe',
        },
        {
            'timestamp': 'Jan. 3, 2020, midnight',
            'type': 'manager.user.deactivation',
            'user': '-',
            'message': 'automatic deactivation because the associated LDAP account does not exist anymore',
        },
        {
            'timestamp': 'Jan. 3, 2020, 1 a.m.',
            'type': 'manager.user.deactivation',
            'user': '-',
            'message': 'automatic deactivation because the associated LDAP source has been deleted',
        },
        {
            'message': 'automatic activation because the associated LDAP account reappeared',
            'timestamp': 'Jan. 3, 2020, 2 a.m.',
            'type': 'manager.user.activation',
            'user': '-',
        },
        {
            'message': 'refusal of single sign on with "service"',
            'timestamp': 'Jan. 3, 2020, 3 a.m.',
            'type': 'user.service.sso.refusal',
            'user': 'Johnny doe',
        },
        {
            'message': 'was denied single sign on with "service"',
            'timestamp': 'Jan. 3, 2020, 4 a.m.',
            'type': 'user.service.sso.denial',
            'user': 'Johnny doe',
        },
        {
            'message': 'notification sent to "user@example.com" after 120 days of '
            'inactivity. Account will be deleted in 20 days.',
            'timestamp': 'Jan. 3, 2020, 8 a.m.',
            'type': 'user.notification.inactivity',
            'user': 'Johnny doe',
        },
        {
            'message': 'user deletion after 140 days of inactivity, notification sent to '
            '"user@example.com".',
            'timestamp': 'Jan. 3, 2020, 9 a.m.',
            'type': 'user.deletion.inactivity',
            'user': 'Johnny doe',
        },
        {
            'message': 'user activity notified by service "service"',
            'timestamp': 'Jan. 3, 2020, 6 p.m.',
            'type': 'user.notification.activity',
            'user': '-',
        },
        {
            'message': 'user activity notified by user "super user"',
            'timestamp': 'Jan. 3, 2020, 7 p.m.',
            'type': 'user.notification.activity',
            'user': 'super user',
        },
        {
            'message': 'adding role "role1" to user "Johnny doe"',
            'timestamp': 'Jan. 4, 2020, 4 a.m.',
            'type': 'auth.oidc.add_role_action',
            'user': 'Johnny doe',
        },
    ]


def test_role_journal(app, superuser, events):
    response = login(app, user=superuser, path='/manage/')
    role1 = Role.objects.get(name='role1')
    role2 = Role.objects.get(name='role2')

    response = app.get('/manage/roles/%s/journal/' % role1.id)
    content = extract_journal(response)

    assert content == [
        {
            'message': 'creation',
            'timestamp': 'Jan. 2, 2020, 9 a.m.',
            'type': 'manager.role.creation',
            'user': 'agent',
        },
        {
            'message': 'edit (name)',
            'timestamp': 'Jan. 2, 2020, 10 a.m.',
            'type': 'manager.role.edit',
            'user': 'agent',
        },
        {
            'message': 'deletion',
            'timestamp': 'Jan. 2, 2020, 11 a.m.',
            'type': 'manager.role.deletion',
            'user': 'agent',
        },
        {
            'message': 'membership grant to user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, noon',
            'type': 'manager.role.membership.grant',
            'user': 'agent',
        },
        {
            'message': 'membership removal of user "Johnny doe"',
            'timestamp': 'Jan. 2, 2020, 1 p.m.',
            'type': 'manager.role.membership.removal',
            'user': 'agent',
        },
        {
            'message': 'inheritance addition from parent role "role2"',
            'timestamp': 'Jan. 2, 2020, 2 p.m.',
            'type': 'manager.role.inheritance.addition',
            'user': 'agent',
        },
        {
            'message': 'inheritance removal from parent role "role2"',
            'timestamp': 'Jan. 2, 2020, 3 p.m.',
            'type': 'manager.role.inheritance.removal',
            'user': 'agent',
        },
        {
            'message': 'addition of role "role2" as administrator',
            'timestamp': 'Jan. 2, 2020, 4 p.m.',
            'type': 'manager.role.administrator.role.addition',
            'user': 'agent',
        },
        {
            'message': 'removal of role "role2" as administrator',
            'timestamp': 'Jan. 2, 2020, 5 p.m.',
            'type': 'manager.role.administrator.role.removal',
            'user': 'agent',
        },
        {
            'message': 'addition of user "Johnny doe" as administrator',
            'timestamp': 'Jan. 2, 2020, 6 p.m.',
            'type': 'manager.role.administrator.user.addition',
            'user': 'agent',
        },
        {
            'message': 'removal of user "Johnny doe" as administrator',
            'timestamp': 'Jan. 2, 2020, 7 p.m.',
            'type': 'manager.role.administrator.user.removal',
            'user': 'agent',
        },
        {
            'message': 'adding role "role1" to user "Johnny doe"',
            'timestamp': 'Jan. 4, 2020, 4 a.m.',
            'type': 'auth.oidc.add_role_action',
            'user': 'Johnny doe',
        },
    ]

    response = app.get('/manage/roles/%s/journal/' % role2.id)
    content = extract_journal(response)

    assert content == [
        {
            'message': 'inheritance addition to child role "role1"',
            'timestamp': 'Jan. 2, 2020, 2 p.m.',
            'type': 'manager.role.inheritance.addition',
            'user': 'agent',
        },
        {
            'message': 'inheritance removal to child role "role1"',
            'timestamp': 'Jan. 2, 2020, 3 p.m.',
            'type': 'manager.role.inheritance.removal',
            'user': 'agent',
        },
        {
            'message': 'addition as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 4 p.m.',
            'type': 'manager.role.administrator.role.addition',
            'user': 'agent',
        },
        {
            'message': 'removal as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 5 p.m.',
            'type': 'manager.role.administrator.role.removal',
            'user': 'agent',
        },
    ]


def test_roles_journal(app, superuser, events):
    response = login(app, user=superuser, path='/manage/')
    response = response.click('Role')
    response = response.click('Journal')

    content = extract_journal(response)

    assert content == [
        {
            'message': 'creation of role "role1"',
            'timestamp': 'Jan. 2, 2020, 9 a.m.',
            'type': 'manager.role.creation',
            'user': 'agent',
        },
        {
            'message': 'edit of role "role1" (name)',
            'timestamp': 'Jan. 2, 2020, 10 a.m.',
            'type': 'manager.role.edit',
            'user': 'agent',
        },
        {
            'message': 'deletion of role "role1"',
            'timestamp': 'Jan. 2, 2020, 11 a.m.',
            'type': 'manager.role.deletion',
            'user': 'agent',
        },
        {
            'message': 'membership grant to user "Johnny doe" in role "role1"',
            'timestamp': 'Jan. 2, 2020, noon',
            'type': 'manager.role.membership.grant',
            'user': 'agent',
        },
        {
            'message': 'membership removal of user "Johnny doe" from role "role1"',
            'timestamp': 'Jan. 2, 2020, 1 p.m.',
            'type': 'manager.role.membership.removal',
            'user': 'agent',
        },
        {
            'message': 'inheritance addition from parent role "role2" to child role "role1"',
            'timestamp': 'Jan. 2, 2020, 2 p.m.',
            'type': 'manager.role.inheritance.addition',
            'user': 'agent',
        },
        {
            'message': 'inheritance removal from parent role "role2" to child role "role1"',
            'timestamp': 'Jan. 2, 2020, 3 p.m.',
            'type': 'manager.role.inheritance.removal',
            'user': 'agent',
        },
        {
            'message': 'addition of role "role2" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 4 p.m.',
            'type': 'manager.role.administrator.role.addition',
            'user': 'agent',
        },
        {
            'message': 'removal of role "role2" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 5 p.m.',
            'type': 'manager.role.administrator.role.removal',
            'user': 'agent',
        },
        {
            'message': 'addition of user "Johnny doe" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 6 p.m.',
            'type': 'manager.role.administrator.user.addition',
            'user': 'agent',
        },
        {
            'message': 'removal of user "Johnny doe" as administrator of role "role1"',
            'timestamp': 'Jan. 2, 2020, 7 p.m.',
            'type': 'manager.role.administrator.user.removal',
            'user': 'agent',
        },
        {
            'message': 'adding role "role1" to user "Johnny doe"',
            'timestamp': 'Jan. 4, 2020, 4 a.m.',
            'type': 'auth.oidc.add_role_action',
            'user': 'Johnny doe',
        },
    ]


def test_date_navigation(app, superuser, events):
    response = login(app, user=superuser, path='/manage/journal/')
    response = response.click('2020')
    assert not response.context['form'].errors

    response = response.click('January')
    response = response.click('^1$')

    content = extract_journal(response)
    assert all(item['timestamp'].startswith('Jan. 1, 2020') for item in content)

    response = response.click('January 2020')
    response = response.click('2020')
    response = response.click('Journal - All dates')


def test_search(app, superuser, events):
    response = login(app, user=superuser, path='/manage/journal/')
    response.form.set('search', 'event:registration')
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 2

    response.form.set('search', 'username:agent event:login')
    response = response.form.submit()
    pq = response.pyquery
    assert [
        list(map(text_content, p))
        for p in zip(pq('tbody td.journal-list--user-column'), pq('tbody td.journal-list--message-column'))
    ] == [
        ['agent', 'login using SAML'],
        ['-', 'login failure with username "agent" on authenticator SAML - saml'],
    ]

    response.form.set('search', 'uuid:%s event:reset' % events['user'].uuid)
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 2

    response.form.set('search', 'session:1234')
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 15
    assert all(
        text_content(node) == 'Johnny doe'
        for node in response.pyquery('tbody tr td.journal-list--user-column')
    )

    response.form.set('search', 'email:jane@example.com')
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 1
    assert (
        text_content(response.pyquery('tbody tr td.journal-list--message-column')[0]).strip()
        == 'email change of user "Johnny doe" for email address "jane@example.com"'
    )

    response.form.set('search', 'jane@example.com')
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 1
    assert (
        text_content(response.pyquery('tbody tr td.journal-list--message-column')[0]).strip()
        == 'email change of user "Johnny doe" for email address "jane@example.com"'
    )

    response.form.set('search', 'johny doe event:login')
    response = response.form.submit()
    pq = response.pyquery

    assert [
        list(map(text_content, p))
        for p in zip(pq('tbody td.journal-list--user-column'), pq('tbody td.journal-list--message-column'))
    ] == [['Johnny doe', 'login using password']]

    Event.objects.filter(type__name='manager.user.creation').update(api=True)
    response.form.set('search', 'api:true')
    response = response.form.submit()
    assert (
        text_content(response.pyquery('tbody tr td.journal-list--message-column')[0]).strip()
        == 'creation of user "Johnny doe"'
    )

    response.form.set('search', 'how:france-connect')
    response = response.form.submit()
    table_content = [text_content(p) for p in response.pyquery('tbody td.journal-list--message-column')]
    assert table_content == ['registration using FranceConnect']

    response.form.set('search', 'how:saml')
    response = response.form.submit()
    table_content = [text_content(p) for p in response.pyquery('tbody td.journal-list--message-column')]
    assert table_content == ['login using SAML']

    response.form.set('search', '')
    response.form['event_type'].select(text='Profile changes')
    response = response.form.submit()

    table_content = [text_content(p) for p in response.pyquery('tbody td.journal-list--message-column')]
    assert table_content == [
        'password reset',
        'user deletion after 140 days of inactivity, notification sent to "user@example.com".',
        'profile "aaa" of type "One Type" deleted for user "Johnny doe"',
        'profile "aaa" of type "One Type" updated for user "Johnny doe"',
        'profile "aaa" of type "One Type" created for user "Johnny doe"',
        'automatic activation of user "Johnny doe" because the associated LDAP account reappeared',
        'automatic deactivation of user "Johnny doe" because the associated LDAP source has been deleted',
        'automatic deactivation of user "Johnny doe" because the associated LDAP account does not exist'
        ' anymore',
        'deactivation of user "Johnny doe"',
        'activation of user "Johnny doe"',
        'mandatory password change at next login unset for user "Johnny doe"',
        'mandatory password change at next login set for user "Johnny doe"',
        'password reset request of "Johnny doe" sent to "user@example.com"',
        'password change of user "Johnny doe" and notification by mail',
        'password change of user "Johnny doe"',
        'email change of user "Johnny doe" for email address "jane@example.com"',
        'edit of user "Johnny doe" (first name)',
        'password reset',
        'password reset failure with email "USER@example.com"',
        'password reset request with email "user@example.com"',
    ]
    response = response.click('Previous')
    table_content = [text_content(p) for p in response.pyquery('tbody td.journal-list--message-column')]
    assert table_content[-3:] == ['user deletion', 'profile edit (first name)', 'password change']

    response.form['event_type'].select(text='Role management')
    response = response.form.submit()

    table_content = [text_content(p) for p in response.pyquery('tbody td.journal-list--message-column')]
    assert table_content[:3] == [
        'removal of user "Johnny doe" as administrator of role "role1"',
        'addition of user "Johnny doe" as administrator of role "role1"',
        'removal of role "role2" as administrator of role "role1"',
    ]

    response.form['event_type'].select(text='User deletions')
    response = response.form.submit()

    table_content = [text_content(p) for p in response.pyquery('tbody td.journal-list--message-column')]
    assert table_content == ['deletion of user "Johnny doe"', 'user deletion']


def test_search_empty(app, superuser, events):
    response = login(app, user=superuser, path='/manage/journal/')
    response.form.set('search', 'abcd123')
    response = response.form.submit()
    assert 'No event found.' in response.text

    user = User.objects.create(username='new_user')
    response = app.get('/manage/users/%s/journal/' % user.pk)
    assert 'Journal is empty.' in response.text


def test_delete_user(app, superuser, events):
    old_user_id = events['user'].id
    events['user'].delete()
    response = login(app, user=superuser, path='/manage/journal/')
    response.form.set('search', events['user'].email)
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 20
    assert 'deletion of user &quot;deleted user (#%s, user@example.com)&quot;' % old_user_id in str(response)


def test_event_type_list(app, superuser, events):
    response = login(app, user=superuser, path='/manage/journal/')
    response = response.click('View available event types')

    for e in Event.objects.all():
        assert '%s (%s)' % (e.type.name, e.type.definition.label) in response.text


def test_delete_authenticator(app, superuser, events):
    events['authenticator'].delete()
    response = login(app, superuser, path='/manage/journal/')
    response.form.set('search', 'event:authenticator.creation')
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 1
    assert 'creation of authenticator "Password"' in response.pyquery('tbody tr').text()


def test_empty_event_types(app, superuser, events):
    '''Check that filtering by a/some non existing event type/s show an empty event's table.'''
    # keep only sso events and event type
    Event.objects.exclude(type__name__contains='sso').delete()
    EventType.objects.exclude(name__contains='sso').delete()
    # try to list only the deletion events (there should be none)
    login(app, user=superuser)
    response = app.get('/manage/journal/')
    response.form['event_type'].select(text='User deletions')
    response = response.form.submit()
    assert len(response.pyquery('tbody tr[data-event-type]')) == 0


def test_search_by_uuid(app, superuser, events):
    login(app, user=superuser)
    response = app.get('/manage/journal/')

    response.form.set('search', 'uuid:%s' % events['user'].uuid)
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) > 0

    events['user'].delete()

    response.form.set('search', 'uuid:%s' % events['user'].uuid)
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) > 0

    DeletedUser.objects.all().delete()

    response.form.set('search', 'uuid:%s' % events['user'].uuid)
    response = response.form.submit()
    assert len(response.pyquery('tbody tr')) == 0


@pytest.mark.parametrize('retention,expected', ((42, 'retained for 42 days'),))
def test_event_type_retention_str(app, superuser, retention, expected):
    with clean_registry():

        class DummyEventType(EventTypeDefinition):  # pylint: disable=unused-variable
            retention_days = retention
            name = 'test.dummy_event'
            label = 'Test event'

        evt_type = event_type_cache('test.dummy_event')
        assert evt_type.retention_days_str == expected


def test_event_types_listing(app, superuser):
    login(app, user=superuser)
    response = app.get('/manage/journal/event-types/')

    for evt_typename in _registry:
        evt_type = EventType.objects.get(name=evt_typename)
        assert (
            f'{evt_typename} ({evt_type.definition.label}) : {evt_type.retention_days_str}' in response.text
        )
