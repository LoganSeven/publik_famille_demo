from django.db import models
from django.db.models.query import ModelIterable


class AuthenticatorIterable(ModelIterable):
    def __iter__(self):
        for obj in ModelIterable(self.queryset):
            yield next(getattr(obj, field) for field in self.queryset.subclasses if hasattr(obj, field))


class AuthenticatorQuerySet(models.QuerySet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subclasses = [
            field.name for field in self.model._meta.get_fields() if isinstance(field, models.OneToOneRel)
        ]
        self._iterable_class = AuthenticatorIterable


class AuthenticatorManager(models.Manager):
    def get_queryset(self):
        qs = AuthenticatorQuerySet(self.model, using=self._db)
        return qs.select_related(*qs.subclasses)
