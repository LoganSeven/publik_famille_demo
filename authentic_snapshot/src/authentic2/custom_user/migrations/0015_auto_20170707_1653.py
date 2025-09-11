from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0014_set_email_verified'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='user',
            options={
                'ordering': ('last_name', 'first_name', 'email', 'username'),
                'verbose_name': 'user',
                'verbose_name_plural': 'users',
            },
        ),
    ]
