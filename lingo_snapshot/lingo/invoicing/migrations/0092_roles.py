import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('invoicing', '0091_invoice_callbacks'),
    ]

    operations = [
        migrations.RenameField(
            model_name='regie',
            old_name='cashier_role',
            new_name='control_role',
        ),
        migrations.AlterField(
            model_name='regie',
            name='control_role',
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='auth.group',
                verbose_name='Control role',
            ),
        ),
        migrations.AddField(
            model_name='regie',
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
            model_name='regie',
            name='invoice_role',
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='auth.group',
                verbose_name='Invoice role',
            ),
        ),
        migrations.AddField(
            model_name='regie',
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
