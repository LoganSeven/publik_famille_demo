from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('a2_rbac', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='external_id',
            field=models.TextField(db_index=True, verbose_name='external id', blank=True),
            preserve_default=True,
        ),
    ]
