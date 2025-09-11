from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0021_attribute_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='attribute',
            name='scopes',
            field=models.CharField(
                default='',
                help_text='scopes separated by spaces',
                max_length=256,
                verbose_name='scopes',
                blank=True,
            ),
        ),
    ]
