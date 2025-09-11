from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0015_organizationalunit_validate_emails'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='organizationalunit',
            options={
                'ordering': ('name',),
                'verbose_name': 'organizational unit',
                'verbose_name_plural': 'organizational units',
            },
        ),
    ]
