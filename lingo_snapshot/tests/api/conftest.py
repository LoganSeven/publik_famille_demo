import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def user():
    user = User.objects.create(
        username='john.doe', first_name='John', last_name='Doe', email='john.doe@example.net'
    )
    user.set_password('password')
    user.save()
    return user
