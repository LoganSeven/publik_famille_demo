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

import datetime
import importlib
from io import BufferedReader, BufferedWriter, TextIOWrapper

import py
import pytest
import responses
import webtest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils.timezone import now
from mellon.models import Issuer, UserSAMLIdentifier

from authentic2.a2_rbac.models import (
    ADMIN_OP,
    MANAGE_MEMBERS_OP,
    SEARCH_OP,
    Operation,
    OrganizationalUnit,
    Permission,
    Role,
)
from authentic2.a2_rbac.utils import get_default_ou, get_operation
from authentic2.apps.journal.models import Event
from authentic2.custom_user.models import DeletedUser
from authentic2.models import UserExternalId
from authentic2_auth_oidc.models import OIDCAccount, OIDCProvider

from .utils import call_command, login

User = get_user_model()


def test_changepassword(db, simple_user, monkeypatch):
    import getpass

    def _getpass(*args, **kwargs):
        return 'pass'

    monkeypatch.setattr(getpass, 'getpass', _getpass)
    call_command('changepassword', 'user')
    old_pass = simple_user.password
    simple_user.refresh_from_db()
    assert old_pass != simple_user.password


def test_clean_unused_account(db, simple_user, mailoutbox, freezer, settings):
    settings.LDAP_AUTH_SETTINGS = [{'realm': 'ldap', 'url': 'ldap://ldap.com/', 'basedn': 'dc=ldap,dc=com'}]
    ldap_user = User.objects.create(username='ldap-user', email='ldap-user@example.com', ou=simple_user.ou)
    oidc_user = User.objects.create(username='oidc-user', email='oidc-user@example.com', ou=simple_user.ou)
    saml_user = User.objects.create(username='saml-user', email='saml-user@example.com', ou=simple_user.ou)
    UserExternalId.objects.create(user=ldap_user, source='ldap', external_id='whatever')
    provider = OIDCProvider.objects.create(name='oidc', ou=simple_user.ou)
    OIDCAccount.objects.create(user=oidc_user, provider=provider, sub='1')

    issuer = Issuer.objects.create(entity_id='https://idp1.example.com/', slug='idp1')
    UserSAMLIdentifier.objects.create(user=saml_user, issuer=issuer, name_id='1234')

    email = simple_user.email
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    last_login = now() - datetime.timedelta(days=2, seconds=30)
    for user in (simple_user, ldap_user, oidc_user, saml_user):
        user.last_login = last_login
        user.save()

    call_command('clean-unused-accounts')

    assert User.objects.count() == 4
    assert len(mailoutbox) == 1
    assert (
        Event.objects.filter(
            type__name='user.notification.inactivity', user=simple_user, data__identifier=simple_user.email
        ).count()
        == 1
    )

    freezer.move_to('2018-01-01 12:00:00')
    # no new mail, no deletion
    call_command('clean-unused-accounts')
    assert User.objects.count() == 4
    assert len(mailoutbox) == 1

    freezer.move_to('2018-01-02')
    call_command('clean-unused-accounts')
    assert User.objects.count() == 3
    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_user_id == simple_user.id
    assert len(mailoutbox) == 2
    assert mailoutbox[-1].to == [email]
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=simple_user, data__identifier=simple_user.email
        ).count()
        == 1
    )


@responses.activate
def test_clean_unused_account_sms(
    db, nomail_user, mailoutbox, freezer, settings, phone_activated_authn, sms_service
):
    settings.LDAP_AUTH_SETTINGS = [{'realm': 'ldap', 'url': 'ldap://ldap.com/', 'basedn': 'dc=ldap,dc=com'}]

    ldap_user = User.objects.create(username='ldap-user', ou=nomail_user.ou)
    ldap_user.attributes.phone = '+33122334455'
    ldap_user.save()
    oidc_user = User.objects.create(username='oidc-user', ou=nomail_user.ou)
    oidc_user.attributes.phone = '+33122334456'
    oidc_user.save()
    saml_user = User.objects.create(username='saml-user', ou=nomail_user.ou)
    saml_user.attributes.phone = '+33122334457'
    saml_user.save()
    UserExternalId.objects.create(user=ldap_user, source='ldap', external_id='whatever')
    provider = OIDCProvider.objects.create(name='oidc', ou=nomail_user.ou)
    OIDCAccount.objects.create(user=oidc_user, provider=provider, sub='1')

    issuer = Issuer.objects.create(entity_id='https://idp1.example.com/', slug='idp1')
    UserSAMLIdentifier.objects.create(user=saml_user, issuer=issuer, name_id='1234')

    freezer.move_to('2018-01-01')
    nomail_user.attributes.phone = '+33611223344'
    nomail_user.save()
    nomail_user.ou.clean_unused_accounts_alert = 2
    nomail_user.ou.clean_unused_accounts_deletion = 3
    nomail_user.ou.save()

    last_login = now() - datetime.timedelta(days=2, seconds=30)
    for user in (nomail_user, ldap_user, oidc_user, saml_user):
        user.last_login = last_login
        user.save()

    call_command('clean-unused-accounts')
    assert sms_service.rsp.call_count == 1
    assert 'Your account is inactive, please log in' in sms_service.last_message
    # check message contains login url
    assert 'https://testserver/login/' in sms_service.last_message

    assert User.objects.count() == 4
    assert len(mailoutbox) == 0
    assert (
        Event.objects.filter(
            type__name='user.notification.inactivity',
            user=nomail_user,
            data__identifier=nomail_user.attributes.phone,
        ).count()
        == 1
    )

    freezer.move_to('2018-01-01 12:00:00')
    # no new sms, no deletion
    call_command('clean-unused-accounts')
    assert sms_service.call_count == 1

    assert User.objects.count() == 4
    assert len(mailoutbox) == 0

    freezer.move_to('2018-01-02')
    call_command('clean-unused-accounts')
    assert sms_service.call_count == 2
    assert 'Your account was inactive and has therefore been deleted.' in sms_service.last_message

    assert User.objects.count() == 3
    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_user_id == nomail_user.id
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity',
            user=nomail_user,
            data__identifier=nomail_user.attributes.phone,
        ).count()
        == 1
    )


def test_clean_unused_account_user_logs_in(app, db, simple_user, mailoutbox, freezer):
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    simple_user.last_login = now() - datetime.timedelta(days=2)
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1

    login(app, simple_user)

    # the day of deletion, nothing happens
    freezer.move_to('2018-01-02')
    simple_user.refresh_from_db()
    assert len(mailoutbox) == 1

    # when new alert delay is reached, user gets alerted again
    freezer.move_to('2018-01-04')
    call_command('clean-unused-accounts')
    simple_user.refresh_from_db()
    assert len(mailoutbox) == 2


def test_clean_unused_account_keepalive(app, db, simple_user, mailoutbox, freezer):
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 20
    simple_user.ou.clean_unused_accounts_deletion = 30
    simple_user.ou.save()

    simple_user.last_login = None
    simple_user.date_joined = datetime.date.fromisoformat('2015-01-01')
    simple_user.keepalive = datetime.date.fromisoformat('2018-01-01')
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0

    freezer.move_to('2018-01-22')

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1

    freezer.move_to('2018-02-23')
    # new command execution is past the theoretical deletion date, however
    # a new keepalive has been pushed in the meantime, the user should not
    # be deleted
    simple_user.keepalive = datetime.date.fromisoformat('2018-02-10')
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1
    assert DeletedUser.objects.count() == 0

    freezer.move_to('2018-03-22')
    call_command('clean-unused-accounts')
    # new alert
    assert len(mailoutbox) == 2
    assert DeletedUser.objects.count() == 0

    freezer.move_to('2018-04-22')
    call_command('clean-unused-accounts')
    # deletion notification
    assert len(mailoutbox) == 3
    assert DeletedUser.objects.get().old_user_id == simple_user.id


def test_clean_unused_account_keepalive_alert_inconsistency_failsafe(
    app, db, simple_user, mailoutbox, freezer
):
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 20
    simple_user.ou.clean_unused_accounts_deletion = 30
    simple_user.ou.save()

    simple_user.last_login = None
    simple_user.date_joined = datetime.date.fromisoformat('2015-01-01')
    simple_user.keepalive = datetime.date.fromisoformat('2018-01-01')
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0

    freezer.move_to('2018-01-22')

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1

    freezer.move_to('2018-02-23')
    # oops, something went wrong, last account deletion alert has not been saved right or has been
    # erroneously modified to a more recent timestamp
    simple_user.last_account_deletion_alert = datetime.date.fromisoformat('2018-02-04')
    # and keepalive has been righteously updated in between
    simple_user.keepalive = datetime.date.fromisoformat('2018-02-03')
    simple_user.save()

    call_command('clean-unused-accounts')
    # no redundant alert, no deletion, i.e. the most restrictive choice in case of such
    # inconsistency
    assert len(mailoutbox) == 1
    assert DeletedUser.objects.count() == 0

    freezer.move_to('2018-02-04')
    # another way things can go wrong: the last alert is right but the keepalive has been updated
    # to an anterior (yet more recent than first execution) date
    simple_user.last_account_deletion_alert = datetime.date.fromisoformat('2018-01-22')
    simple_user.keepalive = datetime.date.fromisoformat('2018-01-21')
    simple_user.save()

    call_command('clean-unused-accounts')
    # no redundant alert, no deletion, i.e. the most restrictive choice also
    assert len(mailoutbox) == 1
    assert DeletedUser.objects.count() == 0


def test_clean_unused_account_never_logged_in(app, db, simple_user, mailoutbox, freezer):
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    simple_user.last_login = None
    simple_user.keepalive = None
    simple_user.date_joined = now() - datetime.timedelta(days=4)
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1

    freezer.move_to('2018-01-03')
    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 2
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=simple_user, data__identifier=simple_user.email
        ).count()
        == 1
    )
    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_user_id == simple_user.id


def test_clean_unused_federated_account_never_logged_in(app, db, simple_user, mailoutbox, freezer, settings):
    freezer.move_to('2018-01-01')
    settings.LDAP_AUTH_SETTINGS = [{'realm': 'ldap', 'url': 'ldap://ldap.com/', 'basedn': 'dc=ldap,dc=com'}]
    ldap_user = User.objects.create(username='ldap-user', email='ldap-user@example.com', ou=simple_user.ou)
    UserExternalId.objects.create(user=ldap_user, source='ldap', external_id='whatever')

    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    simple_user.last_login = simple_user.keepalive = None
    simple_user.date_joined = now() - datetime.timedelta(days=4)
    simple_user.save()

    oidc_provider = OIDCProvider.objects.create(name='Foo', slug='foo', enabled=True)
    OIDCAccount.objects.create(
        provider=oidc_provider,
        user=simple_user,
        sub='abc',
    )

    ldap_user.last_login = ldap_user.keepalive = None
    ldap_user.date_joined = now() - datetime.timedelta(days=4)
    ldap_user.save()

    saml_user = User.objects.create(username='saml-user', email='saml_user@example.com', ou=simple_user.ou)
    saml_issuer = Issuer.objects.create(entity_id='https://idp1.example.com/', slug='idp1')
    UserSAMLIdentifier.objects.create(
        user=saml_user,
        issuer=saml_issuer,
        name_id='1234',
    )

    saml_user.last_login = saml_user.keepalive = None
    saml_user.date_joined = now() - datetime.timedelta(days=4)
    saml_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0

    freezer.move_to('2018-01-03')
    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=simple_user, data__identifier=simple_user.email
        ).count()
        == 1
    )
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=ldap_user, data__identifier=ldap_user.email
        ).count()
        == 0
    )
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=saml_user, data__identifier=saml_user.email
        ).count()
        == 1
    )
    assert DeletedUser.objects.count() == 2
    assert {deleted.old_user_id for deleted in DeletedUser.objects.all()} == {saml_user.id, simple_user.id}

    ldap_user.last_login = ldap_user.keepalive = now() - datetime.timedelta(days=4)
    ldap_user.date_joined = now() - datetime.timedelta(days=5)
    ldap_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=ldap_user, data__identifier=ldap_user.email
        ).count()
        == 0
    )
    assert DeletedUser.objects.count() == 2


def test_clean_unused_federated_account_logged_in_untouched(app, db, simple_user, mailoutbox, freezer):
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    simple_user.last_login = simple_user.date_joined = now() - datetime.timedelta(days=4)
    simple_user.keepalive = None
    simple_user.save()

    provider = OIDCProvider.objects.create(name='Foo', slug='foo', enabled=True)
    OIDCAccount.objects.create(
        provider=provider,
        user=simple_user,
        sub='abc',
    )

    saml_user = User.objects.create(username='saml-user', email='saml_user@example.com', ou=simple_user.ou)
    saml_issuer = Issuer.objects.create(entity_id='https://idp1.example.com/', slug='idp1')
    UserSAMLIdentifier.objects.create(
        user=saml_user,
        issuer=saml_issuer,
        name_id='1234',
    )

    saml_user.last_login = simple_user.date_joined = now() - datetime.timedelta(days=4)
    saml_user.keepalive = None
    saml_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0

    freezer.move_to('2018-01-03')
    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=simple_user, data__identifier=simple_user.email
        ).count()
        == 0
    )
    assert (
        Event.objects.filter(
            type__name='user.deletion.inactivity', user=saml_user, data__identifier=saml_user.email
        ).count()
        == 0
    )
    assert not DeletedUser.objects.count()


def test_clean_unused_account_keepalive_after_alert(app, db, simple_user, mailoutbox, freezer):
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    simple_user.last_login = now() - datetime.timedelta(days=2)
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1

    login(app, simple_user)

    # the day of deletion, nothing happens
    freezer.move_to('2018-01-02')
    assert len(mailoutbox) == 1

    freezer.move_to('2018-01-03')
    # set keepalive
    simple_user.keepalive = now()
    simple_user.save()

    # when new alert delay is reached, no mail is sent and last_account_deletion_alert is reset
    freezer.move_to('2018-01-04')
    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 1
    simple_user.refresh_from_db()
    assert simple_user.last_account_deletion_alert is None


def test_clean_unused_account_disabled_by_default(db, simple_user, mailoutbox):
    simple_user.last_login = now() - datetime.timedelta(days=2)
    simple_user.save()

    call_command('clean-unused-accounts')
    simple_user.refresh_from_db()
    assert len(mailoutbox) == 0


def test_clean_unused_account_a2_user_exclude(app, db, simple_user, mailoutbox, freezer, settings):
    settings.A2_USER_EXCLUDE = {'username': simple_user.username}
    freezer.move_to('2018-01-01')
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3
    simple_user.ou.save()

    simple_user.last_login = now() - datetime.timedelta(days=2)
    simple_user.save()

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0


def test_clean_unused_account_always_alert(db, simple_user, mailoutbox, freezer):
    simple_user.ou.clean_unused_accounts_alert = 2
    simple_user.ou.clean_unused_accounts_deletion = 3  # one day between alert and actual deletion
    simple_user.ou.save()

    simple_user.last_login = now() - datetime.timedelta(days=4)
    simple_user.save()

    # even if account last login in past deletion delay, an alert is always sent first
    call_command('clean-unused-accounts')
    simple_user.refresh_from_db()
    assert len(mailoutbox) == 1

    # and calling again as no effect, since one day must pass before account is deleted
    call_command('clean-unused-accounts')
    simple_user.refresh_from_db()
    assert len(mailoutbox) == 1


@pytest.mark.parametrize('deletion_delay', [730, 500, 65])
def test_clean_unused_account_displayed_message(simple_user, mailoutbox, deletion_delay):
    simple_user.ou.clean_unused_accounts_alert = deletion_delay - 30
    simple_user.ou.clean_unused_accounts_deletion = deletion_delay
    simple_user.ou.save()
    simple_user.last_login = now() - datetime.timedelta(days=deletion_delay + 30)
    simple_user.save()

    # alert email
    call_command('clean-unused-accounts')
    mail = mailoutbox[0]
    assert mail.subject == 'Alert: Jôhn Dôe, your account is inactive and is pending deletion'
    assert 'Jôhn Dôe' in mail.body
    assert 'In order to keep your account, you must log in within 30 days.' in mail.body

    # deletion email
    simple_user.last_account_deletion_alert = now() - datetime.timedelta(days=31)
    simple_user.save()
    call_command('clean-unused-accounts')
    mail = mailoutbox[1]
    assert mail.subject == 'Notification: Jôhn Dôe, your account has been deleted'
    assert 'Jôhn Dôe' in mail.body
    assert 'Your account was inactive, it has been deleted.' in mail.body


def test_clean_unused_account_login_url(simple_user, mailoutbox):
    simple_user.ou.clean_unused_accounts_alert = 1
    simple_user.ou.clean_unused_accounts_deletion = 2
    simple_user.ou.save()
    simple_user.last_login = now() - datetime.timedelta(days=1)
    simple_user.save()
    call_command('clean-unused-accounts')
    mail = mailoutbox[0]
    assert 'href="https://testserver/login/"' in mail.message().as_string()


def test_clean_unused_account_with_no_email(simple_user, mailoutbox, caplog):
    simple_user.email = ''
    simple_user.ou.clean_unused_accounts_alert = 1
    simple_user.ou.clean_unused_accounts_deletion = 2
    simple_user.ou.save()
    simple_user.last_login = now() - datetime.timedelta(days=1)
    simple_user.save()
    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0
    assert 'clean-unused-accounts failed' not in caplog.text


def test_cleanupauthentic(db):
    call_command('cleanupauthentic')


def test_load_ldif(db, monkeypatch, tmpdir):
    FileType = (TextIOWrapper, BufferedReader, BufferedWriter)
    ldif = tmpdir.join('some.ldif')
    ldif.ensure()

    class MockPArser:
        def __init__(self, *args, **kwargs):
            self.users = []
            assert len(args) == 1
            assert isinstance(args[0], FileType)
            assert kwargs['options']['extra_attribute'] == {'ldap_attr': 'first_name'}
            assert kwargs['options']['result'] == 'result'

        def parse(self):
            pass

    oidc_cmd = importlib.import_module('authentic2.management.commands.load-ldif')
    monkeypatch.setattr(oidc_cmd, 'DjangoUserLDIFParser', MockPArser)
    call_command('load-ldif', ldif.strpath, result='result', extra_attribute={'ldap_attr': 'first_name'})

    # test ExtraAttributeAction
    class MockPArser:  # pylint: disable=E0102
        def __init__(self, *args, **kwargs):
            self.users = []
            assert len(args) == 1
            assert isinstance(args[0], FileType)
            assert kwargs['options']['extra_attribute'] == {'ldap_attr': 'first_name'}
            assert kwargs['options']['result'] == 'result'

        def parse(self):
            pass

    monkeypatch.setattr(oidc_cmd, 'DjangoUserLDIFParser', MockPArser)
    call_command(
        'load-ldif', '--extra-attribute', 'ldap_attr', 'first_name', '--result', 'result', ldif.strpath
    )


def test_resetpassword(simple_user):
    call_command('resetpassword', 'user')
    old_pass = simple_user.password
    simple_user.refresh_from_db()
    assert old_pass != simple_user.password


def test_sync_metadata(db):
    test_file = py.path.local(__file__).dirpath('metadata.xml').strpath
    call_command('sync-metadata', test_file, source='abcd')


def test_check_and_repair_managers_of_roles(db, capsys):
    default_ou = get_default_ou()
    admin_op = get_operation(ADMIN_OP)

    OrganizationalUnit.objects.create(name='Orgunit1', slug='orgunit1')
    role1 = Role.objects.create(name='Role 1', slug='role-1', ou=default_ou)
    perm1 = Permission.objects.create(
        operation=admin_op,
        target_id=role1.id,
        ou=default_ou,
        target_ct=ContentType.objects.get_for_model(Role),
    )

    manager_role1 = Role.objects.create(name='Managers of Role 1', slug='_a2-managers-of-role-role1')
    manager_role1.permissions.add(perm1)
    manager_role1.save()

    call_command('check-and-repair', '--repair', '--noinput')

    captured = capsys.readouterr()
    assert '"Managers of Role 1": no admin scope' in captured.out
    assert 'Managers of Role 1" wrong ou, should be "Default organizational unit"' in captured.out
    assert 'invalid permission "Management / role / Role 1": not manage_members operation' in captured.out
    assert (
        'invalid permission "Management / role / Role 1": not admin_scope and not self manage permission'
        in captured.out
    )
    assert (
        'invalid admin role "Managers of Role 1" wrong ou, should be "Default organizational unit" is "None"'
        in captured.out
    )

    perm1.refresh_from_db()
    assert perm1.ou is None
    manager_role1 = role1.get_admin_role()
    assert manager_role1.ou == get_default_ou()
    assert manager_role1.permissions.count() == 3
    assert manager_role1.permissions.get(
        operation=get_operation(MANAGE_MEMBERS_OP), target_id=manager_role1.id
    )
    assert manager_role1.permissions.get(operation=get_operation(MANAGE_MEMBERS_OP), target_id=role1.id)
    assert manager_role1.permissions.get(
        operation=get_operation(SEARCH_OP),
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).pk,
    )

    manage_members_op = get_operation(MANAGE_MEMBERS_OP)
    perm1.op = manage_members_op
    perm1.save()
    call_command('check-and-repair', '--repair', '--noinput')
    perm1 = Permission.objects.get(operation=manage_members_op, target_id=role1.id)
    assert perm1.ou is None


def test_check_and_delete_unused_permissions(db, capsys, simple_user):
    role1 = Role.objects.create(name='Role1', slug='role1')
    op1 = Operation.objects.create(slug='operation-1')
    used_perm = Permission.objects.create(
        operation=op1, target_id=role1.id, target_ct=ContentType.objects.get_for_model(Role)
    )
    role1.admin_scope = used_perm
    role1.save()
    Permission.objects.create(
        operation=op1, target_id=simple_user.id, target_ct=ContentType.objects.get_for_model(get_user_model())
    )

    call_command('check-and-repair', '--fake', '--noinput')
    n_perm = len(Permission.objects.all())

    call_command('check-and-repair', '--repair', '--noinput')
    assert len(Permission.objects.all()) == n_perm - 1


def test_check_identifiers_uniqueness(db, capsys, settings):
    settings.A2_USERNAME_IS_UNIQUE = False
    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()

    User.objects.create(username='foo', email='foo@example.net', first_name='Toto', last_name='Foo', ou=ou)
    User.objects.create(username='foo', email='bar@example.net', first_name='Bar', last_name='Foo', ou=ou)
    User.objects.create(username='bar', email='bar@example.net', first_name='Tutu', last_name='Bar', ou=ou)

    settings.A2_EMAIL_IS_UNIQUE = True
    settings.A2_USERNAME_IS_UNIQUE = True

    call_command('check-and-repair', '--repair', '--noinput')

    captured = capsys.readouterr()
    assert 'found 2 user accounts with same username' in captured.out
    assert 'found 2 user accounts with same email' in captured.out


def test_clean_unused_account_max_mails_per_period(settings, db, mailoutbox, freezer):
    ou = get_default_ou()
    ou.clean_unused_accounts_alert = 1
    ou.clean_unused_accounts_deletion = 2
    ou.save()
    settings.A2_CLEAN_UNUSED_ACCOUNTS_MAX_MAIL_PER_PERIOD = 4

    for i in range(100):
        User.objects.create(ou=ou, email='user-%s@example.com' % i, last_login=now())

    call_command('clean-unused-accounts')
    assert len(mailoutbox) == 0

    freezer.move_to(datetime.timedelta(days=1))
    call_command('clean-unused-accounts')
    # 4 alerts
    assert len(mailoutbox) == 4

    freezer.move_to(datetime.timedelta(days=1))
    call_command('clean-unused-accounts')
    # 4 new alerts and 4 deletions notifications
    assert len(mailoutbox) == 4 + 8


def test_clean_user_exports(settings, app, superuser, freezer):
    users = [User(username='user%s' % i) for i in range(10)]
    User.objects.bulk_create(users)

    # export directory does not exist yet
    call_command('clean-user-exports')

    resp = login(app, superuser, '/manage/users/')
    resp = resp.click('CSV').follow()
    file_creation_time = now()
    assert resp.click('Download CSV')

    freezer.move_to(file_creation_time + datetime.timedelta(days=5))
    call_command('clean-user-exports')
    assert resp.click('Download CSV')

    freezer.move_to(file_creation_time + datetime.timedelta(days=8))
    call_command('clean-user-exports')
    with pytest.raises(webtest.app.AppError):
        resp.click('Download CSV')
