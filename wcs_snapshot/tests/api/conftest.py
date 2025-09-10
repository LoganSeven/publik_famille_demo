import pytest
from quixote import get_publisher

from wcs.qommon.ident.password_accounts import PasswordAccount


@pytest.fixture
def local_user():
    get_publisher().user_class.wipe()
    user = get_publisher().user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.name_identifiers = ['0123456789']
    user.store()

    account = PasswordAccount(id='user')
    account.set_password('user')
    account.user_id = user.id
    account.store()

    return user


@pytest.fixture
def admin_user():
    user = get_publisher().user_class()
    user.name = 'John Doe Admin'
    user.email = 'john.doe@example.com'
    user.name_identifiers = ['0123456789']
    user.is_admin = True
    user.store()

    account = PasswordAccount(id='admin')
    account.set_password('admin')
    account.user_id = user.id
    account.store()

    return user
