import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('epayment', '0004_transaction_invoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentbackend',
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
            model_name='paymentbackend',
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
