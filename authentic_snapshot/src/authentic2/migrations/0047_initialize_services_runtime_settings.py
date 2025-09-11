from django.db import migrations


def initialize_services_runtime_settings(apps, schema_editor):
    from authentic2.utils.misc import RUNTIME_SETTINGS

    Setting = apps.get_model('authentic2', 'Setting')

    if Setting.objects.filter(key__startswith='sso:').count() == 4:
        return
    for key, data in RUNTIME_SETTINGS.items():
        if key.startswith('sso:'):
            Setting.objects.get_or_create(
                key=key,
                defaults={
                    'value': data['value'],
                },
            )


def clear_services_runtime_settings(apps, schema_editor):
    Setting = apps.get_model('authentic2', 'Setting')

    # default config has been extended, do not try to revert it
    if Setting.objects.filter(key__startswith='sso:').count() != 4:
        return

    Setting.objects.filter(key__startswith='sso:').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0046_runtimesetting'),
    ]

    operations = [
        migrations.RunPython(
            initialize_services_runtime_settings, reverse_code=clear_services_runtime_settings
        ),
    ]
