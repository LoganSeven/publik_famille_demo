# families/models.py
from django.conf import settings
from django.db import models

class Child(models.Model):
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='children')
    first_name = models.CharField('Prénom', max_length=100)
    last_name = models.CharField('Nom', max_length=100)
    birth_date = models.DateField('Date de naissance')

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @classmethod
    def create(cls, **kwargs):
        """
        Proxy method used by the tests to create a Child instance.
        Delegates to the default manager (objects.create) so that tests
        can call Child.create(...) or Child.objects.create(...).
        """
        return cls.objects.create(**kwargs)
