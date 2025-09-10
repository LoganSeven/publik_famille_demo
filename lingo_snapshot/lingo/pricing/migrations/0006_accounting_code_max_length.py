from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0005_snapshot_models'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pricing',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=1000, verbose_name='Accounting code (template)'),
        ),
    ]
