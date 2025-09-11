from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0002_auto_20150410_1823'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='user',
            options={
                'verbose_name': 'user',
                'verbose_name_plural': 'users',
            },
        ),
    ]
