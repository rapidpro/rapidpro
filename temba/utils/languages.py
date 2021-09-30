from collections import OrderedDict

import pycountry

from django.conf import settings

# tweak standard ISO-639-3 names to steer users toward using languages with ISO-639-1 equivalents if possible
NAME_OVERRIDES = {
    "ara": "Arabic",  # ar in ISO-639-1
    "afb": "Arabic (Gulf, ISO-639-3)",
    "acx": "Arabic (Omani, ISO-639-3)",
    "aec": "Arabic (Saidi, ISO-639-3)",
    "arb": "Arabic (Standard, ISO-639-3)",
    "msa": "Malay",  # ms in ISO-639-1
    "zsm": "Malay (Standard, ISO-639-3)",
    "nep": "Nepali",  # ne in ISO-639-1
    "npi": "Nepali (Indiv. lang., ISO-639-3)",
    "swa": "Swahili",  # sw in ISO-639-1
    "swc": "Swahili (Congo, ISO-639-3)",
    "swh": "Swahili (Indiv. lang., ISO-639-3)",
    "zho": "Chinese",  # zh in ISO-639-1
    "cmn": "Chinese (Mandarin, ISO-639-3)",
    "kir": "Kyrgyz",  # https://github.com/rapidpro/rapidpro/issues/1551
}

NAMES = {}


def reload():
    """
    Reloads languages
    """
    global NAMES
    NAMES = {}

    for lang in pycountry.languages:
        is_iso6391 = getattr(lang, "alpha_2", None)
        if is_iso6391 or lang.alpha_3 in settings.NON_ISO6391_LANGUAGES:
            NAMES[lang.alpha_3] = NAME_OVERRIDES.get(lang.alpha_3, lang.name)

    # sort by name
    NAMES = OrderedDict(sorted(NAMES.items(), key=lambda n: n[1]))


reload()


def get_name(code: str) -> str:
    """
    Gets a language name for a given ISO639-3 code.
    Args:
        code: three character ISO639-3 code
    """

    return NAMES.get(code, "")


def search_by_name(query: str):
    """
    Searches language names in ISO639-2. Only returns languages with a 2-letter code, except those
    explicitly allowed by the NON_ISO6391_LANGUAGES setting.
    Args:
        query: substring of a language name, e.g. "Fren"
    Returns:
        A list of dicts showing the matches [{"value": "fra", "name": "French"}]
    """

    query = query.lower()

    return [{"value": code, "name": name} for code, name in NAMES.items() if query in name.lower()]


def choices(codes: set, sort: bool = True) -> tuple:
    """
    Converts language codes into a list of code/name tuples suitable for choices on a form.
    """
    cs = tuple((c, NAMES[c]) for c in codes)
    if sort:
        cs = sorted(cs, key=lambda x: x[1])
    return cs


def alpha2_to_alpha3(alpha_2: str):
    """
    Convert 2-char code (e.g. es) to a 3-char code (e.g. spa)
    """
    lang = pycountry.languages.get(alpha_2=alpha_2[:2])
    return lang.alpha_3 if lang else None
