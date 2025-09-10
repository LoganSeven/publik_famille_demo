from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0011_counter'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='counter_name',
            field=models.CharField(default='{yy}', max_length=50, verbose_name='Counter name'),
        ),
        migrations.AddField(
            model_name='regie',
            name='number_format',
            field=models.CharField(
                default='F{regie_id:02d}-{yy}-{mm}-{number:07d}', max_length=100, verbose_name='Number format'
            ),
        ),
    ]
