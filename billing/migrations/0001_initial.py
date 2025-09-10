# billing/migrations/0001_initial.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('activities', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=8, verbose_name='Montant (€)')),
                ('status', models.CharField(choices=[('UNPAID', 'Non payée'), ('PAID', 'Payée')], default='UNPAID', max_length=16, verbose_name='Statut')),
                ('issued_on', models.DateTimeField(verbose_name='Émise le')),
                ('paid_on', models.DateTimeField(blank=True, null=True, verbose_name='Payée le')),
                ('enrollment', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='invoice', to='activities.enrollment', verbose_name='Inscription')),
            ],
            options={'ordering': ['-issued_on']},
        ),
    ]
