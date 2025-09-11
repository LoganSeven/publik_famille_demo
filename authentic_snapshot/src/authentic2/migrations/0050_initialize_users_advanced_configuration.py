from django.db import migrations


def initialize_users_advanced_config(apps, schema_editor):
    from authentic2.utils.misc import RUNTIME_SETTINGS

    Setting = apps.get_model('authentic2', 'Setting')

    if Setting.objects.filter(key__startswith='users:').count() == 1:
        return
    for key, data in RUNTIME_SETTINGS.items():
        if key == 'users:backoffice_sidebar_template':
            Setting.objects.get_or_create(
                key=key,
                defaults={
                    'value': data['value'],
                },
            )


def clear_users_advanced_config(apps, schema_editor):
    Setting = apps.get_model('authentic2', 'Setting')

    # default config has been extended, do not try to revert it
    if Setting.objects.filter(key__startswith='users:').count() != 1:
        return

    Setting.objects.filter(key__startswith='users:backoffice_sidebar_template').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0049_apiclient_allowed_user_attributes'),
    ]

    operations = [
        migrations.RunPython(initialize_users_advanced_config, reverse_code=clear_users_advanced_config),
    ]
