# activities/management/commands/bootstrap_demo.py
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from families.models import Child
from activities.models import Activity, Enrollment
from billing.models import Invoice


class Command(BaseCommand):
    help = "Crée des utilisateurs, enfants, activités et une inscription + facture de démonstration"

    def handle(self, *args, **options):
        admin, created = User.objects.get_or_create(username='admin', defaults={'is_staff': True, 'is_superuser': True})
        if created:
            admin.set_password('admin123')
            admin.save()
            self.stdout.write(self.style.SUCCESS("Admin: admin / admin123"))
        else:
            self.stdout.write("Admin existe déjà (admin).")

        parent, created = User.objects.get_or_create(username='parent', defaults={'email': 'parent@example.org'})
        if created:
            parent.set_password('parent123')
            parent.save()
            self.stdout.write(self.style.SUCCESS("Parent: parent / parent123"))
        else:
            self.stdout.write("Parent existe déjà (parent).")

        child, _ = Child.objects.get_or_create(parent=parent, first_name='Bob', last_name='Demo', birth_date='2015-06-01')

        canteen, _ = Activity.objects.get_or_create(
            title='Cantine Scolaire Septembre 2025',
            defaults={'description': "Inscription à la cantine pour septembre.", 'fee': 50.00, 'capacity': 100, 'is_active': True}
        )
        camp, _ = Activity.objects.get_or_create(
            title='Stage Sportif Été 2025',
            defaults={'description': "Stage multi-sports.", 'fee': 100.00, 'capacity': 30, 'is_active': True}
        )

        enroll, created = Enrollment.objects.get_or_create(child=child, activity=canteen)
        if created:
            Invoice.objects.get_or_create(enrollment=enroll, defaults={'amount': canteen.fee})
            self.stdout.write(self.style.SUCCESS("Inscription + facture de démo créées (non payée)."))
        else:
            self.stdout.write("Inscription de démo déjà existante.")
