# authentic2 - © Entr'ouvert

import pytest


class AdminTestMixin:
    @pytest.fixture
    def app(self, db, app, admin):
        app.authorization = ('Basic', (admin.username, admin.clear_password))
        return app


class UserTestMixin:
    @pytest.fixture
    def app(self, db, app, user):
        app.authorization = ('Basic', (user.username, user.clear_password))
        return app
