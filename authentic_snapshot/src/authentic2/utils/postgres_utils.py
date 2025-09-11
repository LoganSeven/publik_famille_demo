# authentic2 - (C) Entr'ouvert

from django.contrib.postgres.search import TrigramBase
from django.db.models.lookups import PostgresOperatorLookup


class TrigramStrictWordSimilar(PostgresOperatorLookup):
    lookup_name = 'trigram_strict_word_similar'
    postgres_operator = '%%>>'


class TrigramStrictWordDistance(TrigramBase):
    function = ''
    arg_joiner = ' <->>> '
