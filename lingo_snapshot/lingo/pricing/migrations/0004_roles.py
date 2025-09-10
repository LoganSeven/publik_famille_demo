import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('pricing', '0003_min_pricing_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricing',
            name='edit_role',
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='auth.group',
                verbose_name='Edit role',
            ),
        ),
        migrations.AddField(
            model_name='pricing',
            name='view_role',
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='auth.group',
                verbose_name='View role',
            ),
        ),
    ]
