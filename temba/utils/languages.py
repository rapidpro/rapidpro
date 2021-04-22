import pycountry

from django.conf import settings

iso_codes = {}


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
    language = pycountry.languages.get(alpha_3=iso_code)

    if language:
        lang_name = language.name
    else:
        lang_name = None
    return lang_name


def search_by_name(query: str):
    """
    Searches language names in ISO639-2. Only returns languages with a 2-letter code, except those
    explicitly allowed by the NON_ISO6391_LANGUAGES setting.
    Args:
        query: substring of a language name, e.g. "Fren"
    Returns:
        A list of dicts showing the matches [{"value": "fra", "name": "French"}]
    """
    matches = []
    query = query.lower()

    for lang in pycountry.languages:
        has_alpha_2 = getattr(lang, "alpha_2", None)
        if (has_alpha_2 or lang.alpha_3 in settings.NON_ISO6391_LANGUAGES) and query in lang.name.lower():
            matches.append(dict(value=lang.alpha_3, name=lang.name))
    return matches
