# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pycountry

iso_codes = {}
migration_lang_cache = {}


def get_language_name(iso_code):
    """
    Gets a language name for a given ISO639-3 code.

    Args:
        iso_code: three character iso_code
    """
    if iso_code not in iso_codes:
        iso_codes[iso_code] = _get_language_name_iso6393(iso_code)

    return iso_codes[iso_code]


def _get_language_name_iso6393(iso_code):
    try:
        lang_name = pycountry.languages.get(alpha_3=iso_code).name
    except KeyError:
        lang_name = None
    return lang_name


def search_language_names(query):
    """
    Searches language names in ISO639-2
    Args:
        query: Substring of a language name, 'Frenc'

    Returns:
        A list of dicts showing the matches [{id:'fra', text:'French'}]
    """
    matches = []
    query = query.lower()

    for lang in pycountry.languages:
        if query in lang.name.lower():
            matches.append(dict(id=lang.alpha_3, text=lang.name))
    return matches


# As iso639-2 languages can be broad, not all iso639-2 languages have direct translations to iso639-3. This table
# maps country and iso639-2 codes to a specific iso639-3 language code. It isn't inclusive but covers the cases
# we know about at the time of our 639-2 -> 639-3 migration.
MIGRATION_OVERRIDES = {
    'NG:cpe': 'pcm',
    'LR:cpe': 'lir',
    'NI:cpe': 'bzk',

    'XX:mkh': 'khm',
    'XX:cpe': 'pcm',
    'XX:art': 'epo',
    'XX:cpf': 'gcr',
    'XX:phi': 'fil',
    'XX:smi': 'smj',
    'XX:afa': 'ara',
    'XX:aus': 'rop',
    'XX:cpp': 'kea',
    'XX:him': 'xnr',
    'XX:kar': 'blk',
    'XX:esp': 'spa',
}


def iso6392_to_iso6393(iso_code, country_code=None):
    """
    Given an iso639-2 code and an optional country code, returns the appropriate 639-3 code to use.
    """
    import iso639

    if iso_code is None:
        return None

    iso_code = iso_code.lower().strip()

    if iso_code == '':
        raise ValueError('iso_code must not be empty')

    cache_key = '{}:{}'.format('XX' if country_code is None else country_code, iso_code)

    if cache_key not in migration_lang_cache:

        # build our key
        override_key = '%s:%s' % (country_code, iso_code) if country_code else 'XX:%s' % iso_code
        override = MIGRATION_OVERRIDES.get(override_key)

        if not override and country_code:
            override_key = 'XX:%s' % iso_code
            override = MIGRATION_OVERRIDES.get(override_key)

        if override:
            return override

        else:
            # first try looking up by part 2 bibliographic (which is what we use when available)
            try:
                lang = iso639.languages.get(part2b=iso_code)
            except KeyError:
                lang = None

            # if not found, back down to typographical
            if lang is None:
                try:
                    lang = iso639.languages.get(part2t=iso_code)
                except KeyError:
                    pass
            # if not found, maybe it's already a iso639-3 code
            if lang is None:
                try:
                    lang = iso639.languages.get(part3=iso_code)
                except KeyError:
                    pass

            if lang and lang.part3:
                migration_lang_cache[cache_key] = lang.part3
                return lang.part3
    else:
        return migration_lang_cache[cache_key]

    raise ValueError("unable to determine iso639-3 code: %s (%s)" % (iso_code, country_code))
