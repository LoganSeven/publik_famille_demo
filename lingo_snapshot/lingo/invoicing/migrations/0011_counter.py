import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0010_event_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoice',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoice',
            name='number',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='Counter',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('name', models.CharField(max_length=128)),
                ('value', models.PositiveIntegerField(default=0)),
                (
                    'regie',
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.Regie'),
                ),
            ],
            options={
                'unique_together': {('regie', 'name')},
            },
        ),
    ]
