import copy
from unittest import mock

import pytest
from django.contrib.auth.models import Group

from lingo.invoicing.models import PaymentType, Regie
from lingo.invoicing.utils import export_site, import_site
from lingo.utils.misc import LingoImportError
from tests.invoicing.utils import mocked_requests_send

pytestmark = pytest.mark.django_db


def test_import_export(app):
    Regie.objects.create(label='Foo Bar')

    data = export_site()
    assert len(data['regies']) == 1
    import_site(data={})
    assert Regie.objects.count() == 1


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_import_export_regies(mock_send, app):
    payload = export_site()
    assert len(payload['regies']) == 0

    group1 = Group.objects.create(name='role-foo-1')
    group2 = Group.objects.create(name='role-foo-2')
    group3 = Group.objects.create(name='role-foo-3')
    group4 = Group.objects.create(name='role-foo-4')
    regie = Regie.objects.create(
        label='Foo bar',
        with_campaigns=True,
        description='blah',
        assign_credits_on_creation=False,
        edit_role=group1,
        view_role=group2,
        invoice_role=group3,
        control_role=group4,
        payer_carddef_reference='default:card_model_1',
        payer_external_id_prefix='prefix',
        payer_external_id_template='template',
        payer_external_id_from_nameid_template='nameid_template',
        payer_user_fields_mapping='mapping',
        counter_name='{yyyy}',
        invoice_number_format='Fblah{regie_id:02d}-{yy}-{mm}-{number:07d}',
        payment_number_format='Rblah{regie_id:02d}-{yy}-{mm}-{number:07d}',
        docket_number_format='Bblah{regie_id:02d}-{yy}-{mm}-{number:07d}',
        credit_number_format='Ablah{regie_id:02d}-{yy}-{mm}-{number:07d}',
        refund_number_format='Vblah{regie_id:02d}-{yy}-{mm}-{number:07d}',
        main_colour='#DF5A14',
        invoice_model='full',
        invoice_custom_text='foo bar',
        certificate_model='full',
        controller_name='Foo',
        city_name='Bar',
    )

    payload = export_site()
    assert len(payload['regies']) == 1

    regie.delete()
    Group.objects.all().delete()
    assert not Regie.objects.exists()

    Group.objects.create(name='role')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(copy.deepcopy(payload))
    assert str(excinfo.value) == 'Missing role: role-foo-1'

    group1 = Group.objects.create(name='role-foo-1')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(copy.deepcopy(payload))
    assert str(excinfo.value) == 'Missing role: role-foo-2'

    group2 = Group.objects.create(name='role-foo-2')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(copy.deepcopy(payload))
    assert str(excinfo.value) == 'Missing role: role-foo-3'

    group3 = Group.objects.create(name='role-foo-3')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(copy.deepcopy(payload))
    assert str(excinfo.value) == 'Missing role: role-foo-4'

    group4 = Group.objects.create(name='role-foo-4')
    import_site(copy.deepcopy(payload))
    assert Regie.objects.count() == 1
    regie = Regie.objects.first()
    assert regie.label == 'Foo bar'
    assert regie.slug == 'foo-bar'
    assert regie.with_campaigns is True
    assert regie.description == 'blah'
    assert regie.assign_credits_on_creation is False
    assert regie.payer_carddef_reference == 'default:card_model_1'
    assert regie.payer_external_id_prefix == 'prefix'
    assert regie.payer_external_id_template == 'template'
    assert regie.payer_external_id_from_nameid_template == 'nameid_template'
    assert regie.payer_user_fields_mapping == 'mapping'
    assert regie.edit_role == group1
    assert regie.view_role == group2
    assert regie.invoice_role == group3
    assert regie.control_role == group4
    assert regie.counter_name == '{yyyy}'
    assert regie.invoice_number_format == 'Fblah{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.payment_number_format == 'Rblah{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.docket_number_format == 'Bblah{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.credit_number_format == 'Ablah{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.refund_number_format == 'Vblah{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.main_colour == '#DF5A14'
    assert regie.invoice_model == 'full'
    assert regie.invoice_custom_text == 'foo bar'
    assert regie.certificate_model == 'full'
    assert regie.controller_name == 'Foo'
    assert regie.city_name == 'Bar'

    # update
    update_payload = copy.deepcopy(payload)
    update_payload['regies'][0]['label'] = 'Foo bar Updated'
    import_site(update_payload)
    regie.refresh_from_db()
    assert regie.label == 'Foo bar Updated'

    # insert another regie
    regie.slug = 'foo-bar-updated'
    regie.save()
    import_site(copy.deepcopy(payload))
    assert Regie.objects.count() == 2
    regie = Regie.objects.latest('pk')
    assert regie.label == 'Foo bar'
    assert regie.slug == 'foo-bar'


def test_import_export_regie_with_payment_types(app):
    payload = export_site()
    assert len(payload['regies']) == 0

    regie = Regie.objects.create(label='Foo bar')
    PaymentType.objects.create(label='Foo', regie=regie)
    PaymentType.objects.create(label='Baz', regie=regie)

    payload = export_site()
    assert len(payload['regies']) == 1

    PaymentType.objects.all().delete()
    regie.delete()
    assert not Regie.objects.exists()
    assert not PaymentType.objects.exists()

    import_site(copy.deepcopy(payload))
    assert Regie.objects.count() == 1
    regie = Regie.objects.first()
    assert regie.label == 'Foo bar'
    assert regie.slug == 'foo-bar'
    assert regie.paymenttype_set.count() == 2
    assert PaymentType.objects.get(regie=regie, label='Foo', slug='foo')
    assert PaymentType.objects.get(regie=regie, label='Baz', slug='baz')

    # update
    update_payload = copy.deepcopy(payload)
    update_payload['regies'][0]['label'] = 'Foo bar Updated'
    import_site(update_payload)
    regie.refresh_from_db()
    assert regie.label == 'Foo bar Updated'

    # insert another regie
    regie.slug = 'foo-bar-updated'
    regie.save()
    import_site(copy.deepcopy(payload))
    assert Regie.objects.count() == 2
    regie = Regie.objects.latest('pk')
    assert regie.label == 'Foo bar'
    assert regie.slug == 'foo-bar'
    assert regie.paymenttype_set.count() == 2
    assert PaymentType.objects.get(regie=regie, label='Foo', slug='foo')
    assert PaymentType.objects.get(regie=regie, label='Baz', slug='baz')
