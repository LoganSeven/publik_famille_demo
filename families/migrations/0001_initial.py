#families/migrations/0001_initial.py
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Child',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_name', models.CharField(max_length=100, verbose_name='Prénom')),
                ('last_name', models.CharField(max_length=100, verbose_name='Nom')),
                ('birth_date', models.DateField(verbose_name='Date de naissance')),
                ('parent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='children', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['last_name', 'first_name']},
        ),
    ]
