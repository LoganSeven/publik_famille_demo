import pytest
from quixote import get_publisher


@pytest.fixture
def local_user():
    get_publisher().user_class.wipe()
    user = get_publisher().user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.name_identifiers = ['0123456789']
    user.store()
    return user
