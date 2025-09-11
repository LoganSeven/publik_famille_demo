import django.db.models.manager
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0020_delete_federatedid'),
    ]

    operations = [
        migrations.AddField(
            model_name='attribute',
            name='order',
            field=models.PositiveIntegerField(default=0, verbose_name='order'),
        ),
        migrations.AlterModelOptions(
            name='attribute',
            options={
                'base_manager_name': 'all_objects',
                'ordering': ('order', 'id'),
                'verbose_name': 'attribute definition',
                'verbose_name_plural': 'attribute definitions',
            },
        ),
        migrations.AlterModelManagers(
            name='attribute',
            managers=[
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='attributevalue',
            managers=[
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
    ]
