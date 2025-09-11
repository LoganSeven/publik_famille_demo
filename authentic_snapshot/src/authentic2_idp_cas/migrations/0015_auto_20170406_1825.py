from django.db import migrations, models

field_kwargs = {}


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_cas', '0014_auto_20151204_1606'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='proxy',
            field=models.ManyToManyField(
                help_text='services who can request proxy tickets for this service',
                verbose_name='proxy',
                to='authentic2_idp_cas.Service',
                blank=True,
                **field_kwargs,
            ),
        ),
    ]
