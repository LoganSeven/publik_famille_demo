from django.conf import settings
from django.db import migrations

CONF_KEY = 'users:can_change_email_address'


def initialize_users_can_change_email(apps, schema_editor):
    from authentic2.utils.misc import RUNTIME_SETTINGS

    Setting = apps.get_model('authentic2', 'Setting')

    if Setting.objects.filter(key=CONF_KEY).exists():
        return

    if not hasattr(settings, 'A2_PROFILE_CAN_CHANGE_EMAIL'):
        old_value = RUNTIME_SETTINGS[CONF_KEY]['value']
    else:
        old_value = bool(settings.A2_PROFILE_CAN_CHANGE_EMAIL)

    Setting.objects.create(key=CONF_KEY, value=old_value)


def clear_users_can_change_email(apps, schema_editor):
    Setting = apps.get_model('authentic2', 'Setting')
    Setting.objects.filter(key=CONF_KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0060_apiclient_identifier_unique'),
    ]

    operations = [
        migrations.RunPython(initialize_users_can_change_email, reverse_code=clear_users_can_change_email),
    ]
