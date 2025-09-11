from django.db import migrations


def fix_add_role_condition(apps, schema_editor):
    AddRoleAction = apps.get_model('authenticators', 'AddRoleAction')
    for action in AddRoleAction.objects.all():
        if action.condition == 'attributes. in ""':
            action.condition = ''
            action.save()


class Migration(migrations.Migration):
    dependencies = [
        ('authenticators', '0018_auto_20230927_1519'),
    ]

    operations = [
        migrations.RunPython(fix_add_role_condition, reverse_code=migrations.RunPython.noop),
    ]
