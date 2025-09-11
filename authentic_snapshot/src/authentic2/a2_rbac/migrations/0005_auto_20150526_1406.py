from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0004_auto_20150523_0028'),
    ]

    operations = [
        migrations.AlterField(
            model_name='role',
            name='service',
            field=models.ForeignKey(
                related_name='roles',
                verbose_name='service',
                blank=True,
                to='authentic2.Service',
                null=True,
                on_delete=models.CASCADE,
            ),
            preserve_default=True,
        ),
    ]
