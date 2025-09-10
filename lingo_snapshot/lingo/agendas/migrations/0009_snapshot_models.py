import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('snapshot', '0002_snapshot_models'),
        ('agendas', '0008_unjustified_absence'),
    ]

    operations = [
        migrations.AddField(
            model_name='agenda',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='agenda',
            name='snapshot',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='temporary_instance',
                to='snapshot.agendasnapshot',
            ),
        ),
        migrations.AddField(
            model_name='agenda',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='checktypegroup',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='checktypegroup',
            name='snapshot',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='temporary_instance',
                to='snapshot.checktypegroupsnapshot',
            ),
        ),
        migrations.AddField(
            model_name='checktypegroup',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
