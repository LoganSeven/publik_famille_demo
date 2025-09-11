from django.conf import settings
from django.db import migrations, models


class ThirdPartyAlterField(migrations.AlterField):
    def __init__(self, *args, **kwargs):
        self.app_label = kwargs.pop('app_label')
        super().__init__(*args, **kwargs)

    def state_forwards(self, app_label, state):
        super().state_forwards(self.app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if hasattr(from_state, 'clear_delayed_apps_cache'):
            from_state.clear_delayed_apps_cache()
        super().database_forwards(self.app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        self.database_forwards(app_label, schema_editor, from_state, to_state)

    def __eq__(self, other):
        return (
            (self.__class__ == other.__class__)
            and (self.app_label == other.app_label)
            and (self.name == other.name)
            and (self.model_name.lower() == other.model_name.lower())
            and (self.field.deconstruct()[1:] == other.field.deconstruct()[1:])
        )

    def references_model(self, *args, **kwargs):
        return True

    def references_field(self, *args, **kwargs):
        return True


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0001_initial'),
        ('admin', '__first__'),
    ]

    run_before = [
        ('auth', '0003_auto_20150410_1657'),
    ]

    operations = [
        # Django admin log
        ThirdPartyAlterField(
            app_label='admin',
            model_name='logentry',
            name='user',
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE),
            preserve_default=True,
        ),
    ]
