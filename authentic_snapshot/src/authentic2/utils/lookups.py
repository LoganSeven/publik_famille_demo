from django.contrib.postgres.lookups import Unaccent as PGUnaccent
from django.db.models import CharField, TextField, Transform
from django.db.models.functions import Concat
from django.db.models.functions import ConcatPair as DjConcatPair


class Unaccent(PGUnaccent):
    function = 'public.immutable_unaccent'


class UnaccentTransform(Transform):
    bilateral = True
    lookup_name = 'immutable_unaccent'
    function = 'public.immutable_unaccent'


CharField.register_lookup(UnaccentTransform)
TextField.register_lookup(UnaccentTransform)


class ConcatPair(DjConcatPair):
    """Django ConcatPair does not implement as_postgresql, using CONCAT as a default.

    But we need immutable concatenation, || being immutable while CONCAT is not.
    """

    def as_postgresql(self, compiler, connection):
        return super().as_sql(compiler, connection, template='%(expressions)s', arg_joiner=' || ')


class ImmutableConcat(Concat):
    def _paired(self, expressions):
        if len(expressions) == 2:
            return ConcatPair(*expressions)
        return ConcatPair(expressions[0], self._paired(expressions[1:]))
