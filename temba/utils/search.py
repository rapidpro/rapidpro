# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals


def ngrams(input_text, n=3, start=0):
    """
    Generates ngrams

    i.e. 'hello' -> 'hel', 'ell', 'llo'
    """
    generated_ngrams = set()

    while len(input_text[start:start + n]) == n:
        ngram = input_text[start:start + n]
        if ngram not in generated_ngrams:

            yield ngram
            generated_ngrams.add(ngram)

        start += 1
