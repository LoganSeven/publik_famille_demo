import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('snapshot', '0002_snapshot_models'),
        ('invoicing', '0099_regie_custom_appearance_parameters'),
    ]

    operations = [
        migrations.AddField(
            model_name='payer',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='payer',
            name='snapshot',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='temporary_instance',
                to='snapshot.payersnapshot',
            ),
        ),
        migrations.AddField(
            model_name='payer',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='regie',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='regie',
            name='snapshot',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='temporary_instance',
                to='snapshot.regiesnapshot',
            ),
        ),
        migrations.AddField(
            model_name='regie',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
