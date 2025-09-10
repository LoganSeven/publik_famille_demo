from django.db import migrations


def forward(apps, schema_editor):
    Credit = apps.get_model('invoicing', 'Credit')
    InvoiceCancellationReason = apps.get_model('invoicing', 'InvoiceCancellationReason')
    CreditCancellationReason = apps.get_model('invoicing', 'CreditCancellationReason')
    for old_cancellation_reason in InvoiceCancellationReason.objects.filter(credit__isnull=False).distinct():
        new_cancellation_reason = CreditCancellationReason.objects.create(
            label=old_cancellation_reason.label,
            slug=old_cancellation_reason.slug,
            disabled=old_cancellation_reason.disabled,
        )
        Credit.objects.filter(old_cancellation_reason=old_cancellation_reason).update(
            new_cancellation_reason=new_cancellation_reason
        )


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0121_credit_cancellation_reason'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
