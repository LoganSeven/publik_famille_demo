import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lingo.pricing.models import Criteria, CriteriaCategory
from lingo.snapshot.models import CriteriaCategorySnapshot
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_add_category(app, admin_user):
    app = login(app)
    resp = app.get('/manage/')
    resp = resp.click('Pricing')
    resp = resp.click('Criterias')
    resp = resp.click('New category')
    resp.form['label'] = 'QF'
    resp = resp.form.submit()
    category = CriteriaCategory.objects.latest('pk')
    assert resp.location.endswith('/manage/pricing/criterias/')
    assert category.label == 'QF'
    assert category.slug == 'qf'
    assert CriteriaCategorySnapshot.objects.count() == 1


def test_edit_category(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')
    category2 = CriteriaCategory.objects.create(label='Domicile')

    app = login(app)
    resp = app.get('/manage/pricing/criterias/')
    resp = resp.click(href='/manage/pricing/criteria/category/%s/edit/' % category.pk)
    resp.form['label'] = 'QF Foo'
    resp.form['slug'] = category2.slug
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == ['Criteria category with this Identifier already exists.']

    resp.form['slug'] = 'baz2'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    category.refresh_from_db()
    assert category.label == 'QF Foo'
    assert category.slug == 'baz2'
    assert CriteriaCategorySnapshot.objects.count() == 1


def test_delete_category(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')
    Criteria.objects.create(label='QF 1', category=category)

    app = login(app)
    resp = app.get('/manage/pricing/criterias/')
    resp = resp.click(href='/manage/pricing/criteria/category/%s/delete/' % category.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    assert CriteriaCategory.objects.exists() is False
    assert Criteria.objects.exists() is False
    assert CriteriaCategorySnapshot.objects.count() == 1


def test_add_criteria(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')

    app = login(app)
    resp = app.get('/manage/pricing/criterias/')
    resp = resp.click('Add a criteria')
    resp.form['label'] = 'QF < 1'
    resp.form['condition'] = 'qf < 1 #'
    assert 'slug' not in resp.context['form'].fields
    resp = resp.form.submit()
    assert resp.context['form'].errors['condition'] == ['Invalid syntax.']
    resp.form['condition'] = ''
    resp = resp.form.submit()
    assert resp.context['form'].errors['condition'] == ['This field is required.']
    resp.form['condition'] = 'qf < 1'
    resp = resp.form.submit()
    criteria = Criteria.objects.latest('pk')
    assert resp.location.endswith('/manage/pricing/criterias/')
    assert criteria.label == 'QF < 1'
    assert criteria.category == category
    assert criteria.slug == 'qf-1'
    assert criteria.condition == 'qf < 1'
    assert criteria.order == 1
    assert criteria.default is False
    assert CriteriaCategorySnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/criteria/category/%s/add/' % category.pk)
    resp.form['label'] = 'QF < 1'
    resp.form['condition'] = 'qf < 1'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    criteria = Criteria.objects.latest('pk')
    assert criteria.label == 'QF < 1'
    assert criteria.category == category
    assert criteria.slug == 'qf-1-1'
    assert criteria.condition == 'qf < 1'
    assert criteria.order == 2
    assert criteria.default is False

    resp = app.get('/manage/pricing/criteria/category/%s/add/' % category.pk)
    resp.form['label'] = 'ELSE'
    resp.form['condition'] = 'qf < 1 #'
    resp.form['default'] = True
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    criteria = Criteria.objects.latest('pk')
    assert criteria.label == 'ELSE'
    assert criteria.category == category
    assert criteria.slug == 'else'
    assert criteria.condition == ''
    assert criteria.order == 0
    assert criteria.default is True


def test_edit_criteria(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')
    criteria = Criteria.objects.create(label='QF 1', category=category)
    criteria2 = Criteria.objects.create(label='QF 2', category=category)
    category2 = CriteriaCategory.objects.create(label='Foo')
    criteria3 = Criteria.objects.create(label='foo-bar', category=category2)

    app = login(app)
    resp = app.get('/manage/pricing/criterias/')
    resp = resp.click(href='/manage/pricing/criteria/category/%s/%s/edit/' % (category.pk, criteria.pk))
    resp.form['label'] = 'QF 1 bis'
    resp.form['slug'] = criteria2.slug
    resp.form['condition'] = 'qf <= 1 #'
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == ['Another criteria exists with the same identifier.']
    assert resp.context['form'].errors['condition'] == ['Invalid syntax.']
    resp.form['condition'] = ''
    resp.form['slug'] = criteria3.slug
    resp = resp.form.submit()
    assert resp.context['form'].errors['condition'] == ['This field is required.']

    resp.form['condition'] = 'qf <= 1'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    criteria.refresh_from_db()
    assert criteria.label == 'QF 1 bis'
    assert criteria.slug == 'foo-bar'
    assert criteria.condition == 'qf <= 1'
    assert criteria.order == 1
    assert criteria.default is False
    assert CriteriaCategorySnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/criteria/category/%s/%s/edit/' % (category.pk, criteria.pk))
    resp.form['condition'] = 'qf <= 1 #'
    resp.form['default'] = True
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    criteria.refresh_from_db()
    assert criteria.condition == ''
    assert criteria.order == 0
    assert criteria.default is True


def test_delete_criteria(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')
    criteria = Criteria.objects.create(label='QF 1', category=category)

    app = login(app)
    resp = app.get('/manage/pricing/criterias/')
    resp = resp.click(href='/manage/pricing/criteria/category/%s/%s/delete/' % (category.pk, criteria.pk))
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    assert CriteriaCategory.objects.exists() is True
    assert Criteria.objects.exists() is False
    assert CriteriaCategorySnapshot.objects.count() == 1


def test_reorder_criterias(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')
    criteria1 = Criteria.objects.create(label='QF 1', category=category)
    criteria2 = Criteria.objects.create(label='QF 2', category=category)
    criteria3 = Criteria.objects.create(label='QF 3', category=category)
    criteria4 = Criteria.objects.create(label='QF 4', category=category)
    default1 = Criteria.objects.create(label='ELSE', category=category, default=True)
    default2 = Criteria.objects.create(label='OTHER ELSE', category=category, default=True)
    assert list(category.criterias.filter(default=False).values_list('pk', flat=True).order_by('order')) == [
        criteria1.pk,
        criteria2.pk,
        criteria3.pk,
        criteria4.pk,
    ]
    assert list(
        category.criterias.filter(default=False).values_list('order', flat=True).order_by('order')
    ) == [1, 2, 3, 4]
    assert list(
        category.criterias.filter(default=True).values_list('order', flat=True).order_by('order')
    ) == [0, 0]

    app = login(app)
    # missing get params
    app.get('/manage/pricing/criteria/category/%s/order/' % (category.pk), status=400)

    # bad new-order param
    bad_params = [
        # missing criteria3 in order
        ','.join(str(x) for x in [criteria1.pk, criteria2.pk, criteria4.pk]),
        # criteria1 mentionned twice
        ','.join(str(x) for x in [criteria1.pk, criteria2.pk, criteria3.pk, criteria4.pk, criteria1.pk]),
        # defaults can not be ordered
        ','.join(
            str(x) for x in [criteria1.pk, criteria2.pk, criteria3.pk, criteria4.pk, default1.pk, default2.pk]
        ),
        # not an id
        'foo,1,2,3,4',
        ' 1 ,2,3,4',
    ]
    for bad_param in bad_params:
        app.get(
            '/manage/pricing/criteria/category/%s/order/' % (category.pk),
            params={'new-order': bad_param},
            status=400,
        )
    # not changed
    assert list(category.criterias.filter(default=False).values_list('pk', flat=True).order_by('order')) == [
        criteria1.pk,
        criteria2.pk,
        criteria3.pk,
        criteria4.pk,
    ]
    assert list(
        category.criterias.filter(default=False).values_list('order', flat=True).order_by('order')
    ) == [1, 2, 3, 4]
    assert list(
        category.criterias.filter(default=True).values_list('order', flat=True).order_by('order')
    ) == [0, 0]

    # change order
    app.get(
        '/manage/pricing/criteria/category/%s/order/' % (category.pk),
        params={
            'new-order': ','.join(str(x) for x in [criteria3.pk, criteria1.pk, criteria4.pk, criteria2.pk])
        },
    )
    assert list(category.criterias.filter(default=False).values_list('pk', flat=True).order_by('order')) == [
        criteria3.pk,
        criteria1.pk,
        criteria4.pk,
        criteria2.pk,
    ]
    assert list(
        category.criterias.filter(default=False).values_list('order', flat=True).order_by('order')
    ) == [1, 2, 3, 4]
    assert list(
        category.criterias.filter(default=True).values_list('order', flat=True).order_by('order')
    ) == [0, 0]
    assert CriteriaCategorySnapshot.objects.count() == 1


def test_criteria_category_inspect(app, admin_user):
    category = CriteriaCategory.objects.create(label='QF')
    Criteria.objects.create(label='QF 1', category=category)
    Criteria.objects.create(label='QF 2', category=category)

    app = login(app)
    with CaptureQueriesContext(connection) as ctx:
        app.get('/manage/pricing/criteria/category/%s/inspect/' % category.pk)
        assert len(ctx.captured_queries) == 4
