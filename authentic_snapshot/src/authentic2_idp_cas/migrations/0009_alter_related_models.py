from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2_idp_cas', '0008_alter_foreign_keys'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attribute',
            name='service',
            field=models.ForeignKey(
                verbose_name='service', to='authentic2_idp_cas.Service', on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='ticket',
            name='service',
            field=models.ForeignKey(
                verbose_name='service', to='authentic2_idp_cas.Service', on_delete=models.CASCADE
            ),
            preserve_default=True,
        ),
    ]
