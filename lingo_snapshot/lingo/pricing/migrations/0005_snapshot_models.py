import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('snapshot', '0002_snapshot_models'),
        ('pricing', '0004_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='criteriacategory',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='criteriacategory',
            name='snapshot',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='temporary_instance',
                to='snapshot.criteriacategorysnapshot',
            ),
        ),
        migrations.AddField(
            model_name='criteriacategory',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='pricing',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='pricing',
            name='snapshot',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='temporary_instance',
                to='snapshot.pricingsnapshot',
            ),
        ),
        migrations.AddField(
            model_name='pricing',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
