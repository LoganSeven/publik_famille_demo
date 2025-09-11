from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0015_auto_20160621_1711'),
        ('custom_user', '0011_manual_attribute_values_for_name_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='attribute',
            name='disabled',
            field=models.BooleanField(blank=True, default=False, verbose_name='disabled'),
        ),
    ]
