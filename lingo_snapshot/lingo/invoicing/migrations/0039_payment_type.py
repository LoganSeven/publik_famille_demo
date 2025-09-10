from django.db import migrations

from lingo.invoicing.models import DEFAULT_PAYMENT_TYPES


def forward(apps, schema_editor):
    Regie = apps.get_model('invoicing', 'Regie')
    Payment = apps.get_model('invoicing', 'Payment')
    PaymentType = apps.get_model('invoicing', 'PaymentType')

    for regie in Regie.objects.all():
        for slug, label in DEFAULT_PAYMENT_TYPES:
            payment_type, _ = PaymentType.objects.get_or_create(
                regie=regie, slug=slug, defaults={'label': label}
            )
            Payment.objects.filter(regie=regie, payment_type=slug).update(new_payment_type=payment_type)


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0038_payment_type'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
