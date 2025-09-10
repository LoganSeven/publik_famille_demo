from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0001_squashed_0019_merge_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricing',
            name='accounting_code',
            field=models.CharField(blank=True, max_length=250, verbose_name='Accounting code (template)'),
        ),
    ]
