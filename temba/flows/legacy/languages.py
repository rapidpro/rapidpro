from iso639 import Lang
from iso639.exceptions import InvalidLanguageValue

# As iso639-1 languages can be broad, not all iso639-2 languages have direct translations to iso639-3. This table
# maps country and iso639-1 codes to a specific iso639-3 language code. It isn't inclusive but covers the cases
# we know about at the time of our 639-1 -> 639-3 migration.
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

migration_lang_cache = {}


def iso6391_to_iso6393(iso_code, country_code=None):
    """
    Given an iso639-1 code and an optional country code, returns the appropriate 639-3 code to use.
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
            try:
                lang = Lang(iso_code)
            except InvalidLanguageValue:
                lang = None

            if lang and lang.pt3:
                migration_lang_cache[cache_key] = lang.pt3
                return lang.pt3
    else:
        return migration_lang_cache[cache_key]

    raise ValueError("unable to determine iso639-3 code: %s (%s)" % (iso_code, country_code))
