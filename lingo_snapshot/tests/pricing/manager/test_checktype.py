import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lingo.agendas.models import CheckType, CheckTypeGroup
from lingo.snapshot.models import CheckTypeGroupSnapshot
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_add_group(app, admin_user):
    app = login(app)
    resp = app.get('/manage/pricing/')
    resp = resp.click('Check types')
    resp = resp.click('New group')
    resp.form['label'] = 'Foo bar'
    resp = resp.form.submit()
    group = CheckTypeGroup.objects.latest('pk')
    assert resp.location.endswith('/manage/pricing/check-types/')
    assert group.label == 'Foo bar'
    assert group.slug == 'foo-bar'
    assert CheckTypeGroupSnapshot.objects.count() == 1


def test_edit_group(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    group2 = CheckTypeGroup.objects.create(label='baz')

    app = login(app)
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/edit/' % group.pk)
    resp.form['label'] = 'Foo bar baz'
    resp.form['slug'] = group2.slug
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == ['Check type group with this Identifier already exists.']

    resp.form['slug'] = 'baz2'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    group.refresh_from_db()
    assert group.label == 'Foo bar baz'
    assert group.slug == 'baz2'
    assert CheckTypeGroupSnapshot.objects.count() == 1

    CheckType.objects.create(label='Foo reason', group=group, kind='absence')
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/unexpected-presence/' % group.pk)
    assert resp.form['unexpected_presence'].options == [('', True, '---------')]

    CheckType.objects.create(label='Foo reason', group=group2, kind='presence')
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/unexpected-presence/' % group.pk)
    assert resp.form['unexpected_presence'].options == [('', True, '---------')]

    check_type = CheckType.objects.create(label='Bar reason', group=group, kind='presence')
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/unexpected-presence/' % group.pk)
    assert resp.form['unexpected_presence'].options == [
        ('', True, '---------'),
        (str(check_type.pk), False, 'Bar reason'),
    ]
    resp.form['unexpected_presence'] = check_type.pk
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    group.refresh_from_db()
    assert group.unexpected_presence == check_type
    assert CheckTypeGroupSnapshot.objects.count() == 2

    CheckType.objects.all().delete()
    CheckType.objects.create(label='Foo reason', group=group, kind='presence')
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/unjustified-absence/' % group.pk)
    assert resp.form['unjustified_absence'].options == [('', True, '---------')]

    CheckType.objects.create(label='Foo reason', group=group2, kind='absence')
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/unjustified-absence/' % group.pk)
    assert resp.form['unjustified_absence'].options == [('', True, '---------')]

    check_type = CheckType.objects.create(label='Bar reason', group=group, kind='absence')
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/unjustified-absence/' % group.pk)
    assert resp.form['unjustified_absence'].options == [
        ('', True, '---------'),
        (str(check_type.pk), False, 'Bar reason'),
    ]
    resp.form['unjustified_absence'] = check_type.pk
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    group.refresh_from_db()
    assert group.unjustified_absence == check_type
    assert CheckTypeGroupSnapshot.objects.count() == 3


def test_delete_group(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    CheckType.objects.create(label='Foo reason', group=group)

    app = login(app)
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/delete/' % group.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    assert CheckTypeGroup.objects.exists() is False
    assert CheckType.objects.exists() is False
    assert CheckTypeGroupSnapshot.objects.count() == 1


def test_add_check_type(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')

    app = login(app)
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click('Add a check type')
    resp.form['label'] = 'Foo reason'
    assert 'slug' not in resp.context['form'].fields
    assert 'disabled' not in resp.context['form'].fields
    assert 'colour' not in resp.context['form'].fields
    resp = resp.form.submit()
    check_type = CheckType.objects.latest('pk')
    assert resp.location.endswith('/manage/pricing/check-types/')
    assert check_type.label == 'Foo reason'
    assert check_type.group == group
    assert check_type.slug == 'foo-reason'
    assert check_type.kind == 'absence'
    assert check_type.pricing is None
    assert check_type.pricing_rate is None
    assert check_type.colour == '#FF0000'
    assert CheckTypeGroupSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/check-type/group/%s/add/' % group.pk)
    resp.form['label'] = 'Foo reason'
    resp.form['kind'] = 'presence'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    check_type = CheckType.objects.latest('pk')
    assert check_type.label == 'Foo reason'
    assert check_type.slug == 'foo-reason-1'
    assert check_type.kind == 'presence'
    assert check_type.colour == '#33CC33'


def test_add_check_type_pricing(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')

    app = login(app)
    resp = app.get('/manage/pricing/check-type/group/%s/add/' % group.pk)
    assert 'pricing' in resp.context['form'].fields
    assert 'pricing_rate' in resp.context['form'].fields
    resp.form['label'] = 'Foo reason'
    resp.form['pricing'] = 42
    resp.form['pricing_rate'] = 150
    resp = resp.form.submit()
    assert resp.context['form'].errors['__all__'] == ['Please choose between pricing and pricing rate.']
    resp.form['pricing'] = 0
    resp.form['pricing_rate'] = 0
    resp = resp.form.submit()
    assert resp.context['form'].errors['__all__'] == ['Please choose between pricing and pricing rate.']
    resp.form['pricing'] = 42
    resp.form['pricing_rate'] = None
    resp = resp.form.submit()
    check_type = CheckType.objects.latest('pk')
    assert check_type.pricing == 42
    assert check_type.pricing_rate is None
    resp = app.get('/manage/pricing/check-type/group/%s/add/' % group.pk)
    resp.form['label'] = 'Foo reason'
    resp.form['pricing'] = -42
    resp = resp.form.submit()
    check_type = CheckType.objects.latest('pk')
    assert check_type.pricing == -42
    assert check_type.pricing_rate is None

    resp = app.get('/manage/pricing/check-type/group/%s/add/' % group.pk)
    resp.form['label'] = 'Foo reason'
    resp.form['pricing_rate'] = 150
    resp = resp.form.submit()
    check_type = CheckType.objects.latest('pk')
    assert check_type.pricing is None
    assert check_type.pricing_rate == 150
    resp = app.get('/manage/pricing/check-type/group/%s/add/' % group.pk)
    resp.form['label'] = 'Foo reason'
    resp.form['pricing_rate'] = -50
    resp = resp.form.submit()
    check_type = CheckType.objects.latest('pk')
    assert check_type.pricing is None
    assert check_type.pricing_rate == -50


def test_edit_check_type(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type = CheckType.objects.create(label='Foo reason', group=group, kind='presence')
    check_type2 = CheckType.objects.create(label='Baz', group=group)
    group2 = CheckTypeGroup.objects.create(label='Foo bar')
    check_type3 = CheckType.objects.create(label='Foo bar reason', group=group2, kind='absence')

    app = login(app)
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/%s/edit/' % (group.pk, check_type.pk))
    resp.form['label'] = 'Foo bar reason'
    resp.form['slug'] = check_type2.slug
    resp.form['code'] = 'XX'
    resp.form['colour'] = '#424242'
    resp.form['disabled'] = True
    assert 'kind' not in resp.context['form'].fields
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == ['Another check type exists with the same identifier.']

    resp.form['slug'] = check_type3.slug
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    check_type.refresh_from_db()
    assert check_type.label == 'Foo bar reason'
    assert check_type.slug == 'foo-bar-reason'
    assert check_type.code == 'XX'
    assert check_type.colour == '#424242'
    assert check_type.kind == 'presence'
    assert check_type.pricing is None
    assert check_type.pricing_rate is None
    assert check_type.disabled is True
    assert CheckTypeGroupSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/check-type/group/%s/%s/edit/' % (group2.pk, check_type3.pk))
    resp.form['colour'] = '#424243'
    resp.form.submit().follow()

    check_type3.refresh_from_db()
    assert check_type3.colour == '#424243'
    assert check_type3.kind == 'absence'

    check_type.refresh_from_db()
    assert check_type.colour == '#424242'

    app.get('/manage/pricing/check-type/group/%s/%s/edit/' % (group2.pk, check_type.pk), status=404)


def test_edit_check_type_pricing(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type = CheckType.objects.create(label='Foo reason', group=group)

    app = login(app)
    resp = app.get('/manage/pricing/check-type/group/%s/%s/edit/' % (group.pk, check_type.pk))
    assert 'pricing' in resp.context['form'].fields
    assert 'pricing_rate' in resp.context['form'].fields
    resp.form['pricing'] = 42
    resp.form['pricing_rate'] = 150
    resp = resp.form.submit()
    assert resp.context['form'].errors['__all__'] == ['Please choose between pricing and pricing rate.']
    resp.form['pricing'] = 42
    resp.form['pricing_rate'] = None
    resp = resp.form.submit()
    check_type.refresh_from_db()
    assert check_type.pricing == 42
    assert check_type.pricing_rate is None
    resp = app.get('/manage/pricing/check-type/group/%s/%s/edit/' % (group.pk, check_type.pk))
    resp.form['pricing'] = -42
    resp = resp.form.submit()
    check_type.refresh_from_db()
    assert check_type.pricing == -42
    assert check_type.pricing_rate is None

    resp = app.get('/manage/pricing/check-type/group/%s/%s/edit/' % (group.pk, check_type.pk))
    resp.form['pricing'] = None
    resp.form['pricing_rate'] = 150
    resp = resp.form.submit()
    check_type.refresh_from_db()
    assert check_type.pricing is None
    assert check_type.pricing_rate == 150
    resp = app.get('/manage/pricing/check-type/group/%s/%s/edit/' % (group.pk, check_type.pk))
    resp.form['pricing_rate'] = -50
    resp = resp.form.submit()
    check_type.refresh_from_db()
    assert check_type.pricing is None
    assert check_type.pricing_rate == -50


def test_delete_check_type(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type = CheckType.objects.create(label='Foo reason', group=group)

    app = login(app)
    resp = app.get('/manage/pricing/check-types/')
    resp = resp.click(href='/manage/pricing/check-type/group/%s/%s/delete/' % (group.pk, check_type.pk))
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    assert CheckTypeGroup.objects.exists() is True
    assert CheckType.objects.exists() is False
    assert CheckTypeGroupSnapshot.objects.count() == 1

    group2 = CheckTypeGroup.objects.create(label='Foo bar baz')
    app.get('/manage/pricing/check-type/group/%s/%s/delete/' % (group2.pk, check_type.pk), status=404)


def test_check_type_group_inspect(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type1 = CheckType.objects.create(label='Foo reason', group=group, kind='presence')
    check_type2 = CheckType.objects.create(label='Foo reason', group=group, kind='absence')
    group.unexpected_presence = check_type1
    group.unjustified_absence = check_type2
    group.save()

    app = login(app)
    with CaptureQueriesContext(connection) as ctx:
        app.get('/manage/pricing/check-type/group/%s/inspect/' % group.pk)
        assert len(ctx.captured_queries) == 4
