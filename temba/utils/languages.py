import iso639
import pycountry

from django.conf import settings

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


# As iso639-2 languages can be broad, not all iso639-2 languages have direct translations to iso639-3. This table
# maps country and iso639-2 codes to a specific iso639-3 language code. It isn't inclusive but covers the cases
# we know about at the time of our 639-2 -> 639-3 migration.
MIGRATION_OVERRIDES = {
    "NG:cpe": "pcm",
    "LR:cpe": "lir",
    "NI:cpe": "bzk",
    "XX:mkh": "khm",
    "XX:cpe": "pcm",
    "XX:art": "epo",
    "XX:cpf": "gcr",
    "XX:phi": "fil",
    "XX:smi": "smj",
    "XX:afa": "ara",
    "XX:aus": "rop",
    "XX:cpp": "kea",
    "XX:him": "xnr",
    "XX:kar": "blk",
    "XX:esp": "spa",
}


def iso6392_to_iso6393(iso_code, country_code=None):
    """
    Given an iso639-2 code and an optional country code, returns the appropriate 639-3 code to use.
    """

    if iso_code is None:
        return None

    iso_code = iso_code.lower().strip()

    if iso_code == "":
        raise ValueError("iso_code must not be empty")

    cache_key = "{}:{}".format("XX" if country_code is None else country_code, iso_code)

    if cache_key not in migration_lang_cache:

        # build our key
        override_key = "%s:%s" % (country_code, iso_code) if country_code else "XX:%s" % iso_code
        override = MIGRATION_OVERRIDES.get(override_key)

        if not override and country_code:
            override_key = "XX:%s" % iso_code
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
