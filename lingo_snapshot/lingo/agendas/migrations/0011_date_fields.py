from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0010_check_type_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='checktype',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='checktype',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
    ]
