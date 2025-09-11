from django.db import migrations


def rename_services_runtime_settings(apps, schema_editor):
    Setting = apps.get_model('authentic2', 'Setting')

    if Setting.objects.filter(key__startswith='sso:').count() != 4:
        return
    old_to_new_mapping = {
        'sso:default_service_colour': 'sso:generic_service_colour',
        'sso:default_service_logo_url': 'sso:generic_service_logo_url',
        'sso:default_service_name': 'sso:generic_service_name',
        'sso:default_service_home_url': 'sso:generic_service_home_url',
    }
    for old, new in old_to_new_mapping.items():
        try:
            setting = Setting.objects.get(key=old)
        except Setting.DoesNotExist:
            continue
        else:
            setting.key = new
            setting.save()


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0045_auto_20230117_1513'),
    ]

    operations = [
        migrations.RunPython(
            rename_services_runtime_settings,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
