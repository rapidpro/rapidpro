from __future__ import absolute_import, print_function, unicode_literals

import pycountry

iso_codes = {}


def get_language_name(iso_code):
    """
    Gets a language name for a given ISO639-3 code.

    Args:
        iso_code: three character iso_code
    """
    if iso_code not in iso_codes:
        try:
            lang_name = pycountry.languages.get(alpha_3=iso_code).name
        except KeyError:
            lang_name = None

        iso_codes[iso_code] = lang_name

    return iso_codes[iso_code]


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
