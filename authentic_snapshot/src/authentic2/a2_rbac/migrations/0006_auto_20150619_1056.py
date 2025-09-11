from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0005_auto_20150526_1406'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationalunit',
            name='email_is_unique',
            field=models.BooleanField(blank=True, default=False, verbose_name='Email is unique'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='organizationalunit',
            name='username_is_unique',
            field=models.BooleanField(blank=True, default=False, verbose_name='Username is unique'),
            preserve_default=True,
        ),
    ]
