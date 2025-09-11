from django.db import migrations, transaction
from django.db.migrations.operations.base import Operation
from django.db.utils import InternalError, OperationalError, ProgrammingError


class SafeExtensionOperation(Operation):
    reversible = True

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return
        try:
            with transaction.atomic():
                try:
                    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS %s SCHEMA public' % self.name)
                except (OperationalError, ProgrammingError):
                    # OperationalError if the extension is not available
                    # ProgrammingError in case of denied permission
                    RunSQLIfExtension.extensions_installed = False
        except InternalError:
            # InternalError (current transaction is aborted, commands ignored
            # until end of transaction block) would be raised when django-
            # tenant-schemas set search_path.
            RunSQLIfExtension.extensions_installed = False

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        try:
            with transaction.atomic():
                schema_editor.execute('DROP EXTENSION IF EXISTS %s' % self.name)
        except InternalError:
            # Raised when other objects depend on the extension. This happens in a multitenant
            # context, where extension in installed in schema "public" but referenced in others (via
            # public.gist_trgm_ops). In this case, do nothing, as the query should be successful
            # when last tenant is processed.
            pass


class RunSQLIfExtension(migrations.RunSQL):
    extensions_installed = True

    def __getattribute__(self, name):
        if name == 'sql' and not self.extensions_installed:
            return migrations.RunSQL.noop
        return object.__getattribute__(self, name)


class TrigramExtension(SafeExtensionOperation):
    name = 'pg_trgm'


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0023_index_username'),
    ]

    operations = [
        TrigramExtension(),
        RunSQLIfExtension(
            sql=[
                'CREATE INDEX IF NOT EXISTS custom_user_user_email_trgm_idx ON custom_user_user USING gist'
                ' (LOWER(email) public.gist_trgm_ops)'
            ],
            reverse_sql=['DROP INDEX custom_user_user_email_trgm_idx'],
        ),
    ]
