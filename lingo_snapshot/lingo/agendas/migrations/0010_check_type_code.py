from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0009_snapshot_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='checktype',
            name='code',
            field=models.CharField(blank=True, max_length=10, verbose_name='Code'),
        ),
    ]
