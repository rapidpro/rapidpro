import re
import iso639
from iso639 import NonExistentLanguageError

iso_codes = {}


def get_language_name(iso_code):
    """
    Gets a language name for a given ISO639-2 code.

    Args:
        iso_code: three character iso_code
    """
    if iso_code not in iso_codes:
        try:
            lang = iso639.to_name(iso_code)
        except NonExistentLanguageError:
            return None

        # we only show up to the first semi or paren
        lang = re.split(';|\(', lang)[0].strip()
        iso_codes[iso_code] = lang

    return iso_codes[iso_code]


def search_language_names(query):
    """
    Searches language names in ISO639-2
    Args:
        query: Substring of a language name, 'Frenc'

    Returns:
        A list of dicts showing the matches [{id:'fre', text:'French'}]
    """
    matches = []
    for lang in iso639.data:
        query = query.lower()
        if query in lang['name'].lower():
            matches.append(dict(id=lang['iso639_2_b'], text=lang['name'].strip()))
    return matches
