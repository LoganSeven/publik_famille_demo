# documents/migrations/0001_initial.py
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Document',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('FACTURE', 'Facture')], max_length=32, verbose_name='Type')),
                ('title', models.CharField(max_length=255, verbose_name='Titre')),
                ('file', models.FileField(upload_to='documents/%Y/%m/%d/', verbose_name='Fichier')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Créé le')),
                ('invoice', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='document', to='billing.invoice', verbose_name='Facture liée')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to=settings.AUTH_USER_MODEL, verbose_name='Utilisateur')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
