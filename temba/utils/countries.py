from typing import Optional

import phonenumbers
from django_countries.data import COUNTRIES

from django.utils.translation import gettext_lazy as _

NAMES = COUNTRIES.copy()
NAMES["GB"] = _("United Kingdom")
NAMES["US"] = _("United States")
NAMES["AC"] = _("Ascension Island")
NAMES["XK"] = _("Kosovo")

CALLING_CODES = {
    "AC": (247,),  # Scension Island
    "AF": (93,),  # Afghanistan
    "AX": (35818,),  # Åland Islands
    "AL": (355,),  # Albania
    "DZ": (213,),  # Algeria
    "AS": (1684,),  # American Samoa
    "AD": (376,),  # Andorra
    "AO": (244,),  # Angola
    "AI": (1264,),  # Anguilla
    "AQ": (),  # Antarctica
    "AG": (1268,),  # Antigua and Barbuda
    "AR": (54,),  # Argentina
    "AM": (374,),  # Armenia
    "AW": (297,),  # Aruba
    "AU": (61,),  # Australia
    "AT": (43,),  # Austria
    "AZ": (994,),  # Azerbaijan
    "BS": (1242,),  # Bahamas
    "BH": (973,),  # Bahrain
    "BD": (880,),  # Bangladesh
    "BB": (1246,),  # Barbados
    "BY": (375,),  # Belarus
    "BE": (32,),  # Belgium
    "BZ": (501,),  # Belize
    "BJ": (229,),  # Benin
    "BM": (1441,),  # Bermuda
    "BT": (975,),  # Bhutan
    "BO": (591,),  # Bolivia (Plurinational State of)
    "BQ": (5997,),  # Bonaire, Sint Eustatius and Saba
    "BA": (387,),  # Bosnia and Herzegovina
    "BW": (267,),  # Botswana
    "BV": (),  # Bouvet Island
    "BR": (55,),  # Brazil
    "IO": (246,),  # British Indian Ocean Territory
    "BN": (673,),  # Brunei Darussalam
    "BG": (359,),  # Bulgaria
    "BF": (226,),  # Burkina Faso
    "BI": (257,),  # Burundi
    "CV": (238,),  # Cabo Verde
    "KH": (855,),  # Cambodia
    "CM": (237,),  # Cameroon
    "CA": (1,),  # Canada
    "KY": (1345,),  # Cayman Islands
    "CF": (236,),  # Central African Republic
    "TD": (235,),  # Chad
    "CL": (56,),  # Chile
    "CN": (86,),  # China
    "CX": (6_189_164,),  # Christmas Island
    "CC": (6_189_162,),  # Cocos (Keeling) Islands
    "CO": (57,),  # Colombia
    "KM": (269,),  # Comoros
    "CD": (243,),  # Congo (the Democratic Republic of the)
    "CG": (242,),  # Congo
    "CK": (682,),  # Cook Islands
    "CR": (506,),  # Costa Rica
    "CI": (225,),  # Côte d'Ivoire
    "HR": (385,),  # Croatia
    "CU": (53,),  # Cuba
    "CW": (5999,),  # Curaçao
    "CY": (357,),  # Cyprus
    "CZ": (420,),  # Czech Republic
    "DK": (45,),  # Denmark
    "DJ": (253,),  # Djibouti
    "DM": (1767,),  # Dominica
    "DO": (1809, 1829, 1849),  # Dominican Republic
    "EC": (539,),  # Ecuador
    "EG": (20,),  # Egypt
    "SV": (503,),  # El Salvador
    "GQ": (240,),  # Equatorial Guinea
    "ER": (291,),  # Eritrea
    "EE": (372,),  # Estonia
    "ET": (251,),  # Ethiopia
    "FK": (500,),  # Falkland Islands  [Malvinas]
    "FO": (298,),  # Faroe Islands
    "FJ": (679,),  # Fiji
    "FI": (358,),  # Finland
    "FR": (33,),  # France
    "GF": (594,),  # French Guiana
    "PF": (689,),  # French Polynesia
    "TF": (),  # French Southern Territories
    "GA": (241,),  # Gabon
    "GM": (220,),  # Gambia
    "GE": (995,),  # Georgia
    "DE": (49,),  # Germany
    "GH": (233,),  # Ghana
    "GI": (350,),  # Gibraltar
    "GR": (30,),  # Greece
    "GL": (299,),  # Greenland
    "GD": (1473,),  # Grenada
    "GP": (590,),  # Guadeloupe
    "GU": (1671,),  # Guam
    "GT": (502,),  # Guatemala
    "GG": (441_481, 447_781, 447_839, 447_911),  # Guernsey
    "GN": (224,),  # Guinea
    "GW": (245,),  # Guinea-Bissau
    "GY": (592,),  # Guyana
    "HT": (509,),  # Haiti
    "HM": (),  # Heard Island and McDonald Islands
    "VA": (379, 3_906_698),  # Holy See
    "HN": (504,),  # Honduras
    "HK": (852,),  # Hong Kong
    "HU": (36,),  # Hungary
    "IS": (354,),  # Iceland
    "IN": (91,),  # India
    "ID": (62,),  # Indonesia
    "IR": (98,),  # Iran (Islamic Republic of)
    "IQ": (964,),  # Iraq
    "IE": (353,),  # Ireland
    "IM": (441_624, 447_524, 447_624, 447_924),  # Isle of Man
    "IL": (972,),  # Israel
    "IT": (39,),  # Italy
    "JM": (1876,),  # Jamaica
    "JP": (81,),  # Japan
    "JE": (441_534,),  # Jersey
    "JO": (962,),  # Jordan
    "KZ": (76, 77),  # Kazakhstan
    "KE": (254,),  # Kenya
    "KI": (686,),  # Kiribati
    "KP": (850,),  # Korea (the Democratic People's Republic of)
    "KR": (82,),  # Korea (the Republic of)
    "KW": (965,),  # Kuwait
    "KG": (996,),  # Kyrgyzstan
    "LA": (856,),  # Lao People's Democratic Republic
    "LV": (371,),  # Latvia
    "LB": (961,),  # Lebanon
    "LS": (266,),  # Lesotho
    "LR": (231,),  # Liberia
    "LY": (218,),  # Libya
    "LI": (423,),  # Liechtenstein
    "LT": (370,),  # Lithuania
    "LU": (352,),  # Luxembourg
    "MO": (853,),  # Macao
    "MK": (389,),  # Macedonia (the former Yugoslav Republic of)
    "MG": (261,),  # Madagascar
    "MW": (265,),  # Malawi
    "MY": (60,),  # Malaysia
    "MV": (960,),  # Maldives
    "ML": (223,),  # Mali
    "MT": (356,),  # Malta
    "MH": (692,),  # Marshall Islands
    "MQ": (596,),  # Martinique
    "MR": (222,),  # Mauritania
    "MU": (230,),  # Mauritius
    "YT": (262_269, 262_639),  # Mayotte
    "MX": (52,),  # Mexico
    "FM": (691,),  # Micronesia (Federated States of)
    "MD": (373,),  # Moldova (the Republic of)
    "MC": (377,),  # Monaco
    "MN": (976,),  # Mongolia
    "ME": (382,),  # Montenegro
    "MS": (1664,),  # Montserrat
    "MA": (212,),  # Morocco
    "MZ": (258,),  # Mozambique
    "MM": (95,),  # Myanmar
    "NA": (264,),  # Namibia
    "NR": (674,),  # Nauru
    "NP": (977,),  # Nepal
    "NL": (31,),  # Netherlands
    "NC": (687,),  # New Caledonia
    "NZ": (64,),  # New Zealand
    "NI": (505,),  # Nicaragua
    "NE": (227,),  # Niger
    "NG": (243,),  # Nigeria
    "NU": (683,),  # Niue
    "NF": (6723,),  # Norfolk Island
    "MP": (1670,),  # Northern Mariana Islands
    "NO": (47,),  # Norway
    "OM": (968,),  # Oman
    "PK": (92,),  # Pakistan
    "PW": (680,),  # Palau
    "PS": (970,),  # Palestine, State of
    "PA": (507,),  # Panama
    "PG": (675,),  # Papua New Guinea
    "PY": (595,),  # Paraguay
    "PE": (51,),  # Peru
    "PH": (63,),  # _("Philippines
    "PN": (64,),  # Pitcairn
    "PL": (48,),  # Poland
    "PT": (351,),  # Portugal
    "PR": (1787, 1939),  # Puerto Rico
    "QA": (974,),  # Qatar
    "RE": (262,),  # Réunion
    "RO": (40,),  # Romania
    "RU": (7,),  # Russian Federation
    "RW": (250,),  # Rwanda
    "BL": (590,),  # _("Saint Barthélemy
    "SH": (290,),  # Saint Helena, Ascension and Tristan da Cunha
    "KN": (1869,),  # Saint Kitts and Nevis
    "LC": (1758,),  # Saint Lucia
    "MF": (590,),  # Saint Martin (French part)
    "PM": (508,),  # Saint Pierre and Miquelon
    "VC": (1784,),  # Saint Vincent and the Grenadines
    "WS": (685,),  # Samoa
    "SM": (378,),  # San Marino
    "ST": (239,),  # Sao Tome and Principe
    "SA": (966,),  # Saudi Arabia
    "SN": (221,),  # Senegal
    "RS": (381,),  # Serbia
    "SC": (248,),  # Seychelles
    "SL": (232,),  # Sierra Leone
    "SG": (65,),  # Singapore
    "SX": (1721,),  # Sint Maarten (Dutch part)
    "SK": (421,),  # Slovakia
    "SI": (386,),  # Slovenia
    "SB": (677,),  # Solomon Islands
    "SO": (252,),  # Somalia
    "ZA": (27,),  # South Africa
    "GS": (500,),  # South Georgia and the South Sandwich Islands
    "SS": (211,),  # South Sudan
    "ES": (34,),  # Spain
    "LK": (94,),  # Sri Lanka
    "SD": (249,),  # Sudan
    "SR": (597,),  # Suriname
    "SJ": (4779,),  # Svalbard and Jan Mayen
    "SZ": (268,),  # Swaziland
    "SE": (46,),  # Sweden
    "CH": (41,),  # Switzerland
    "SY": (963,),  # Syrian Arab Republic
    "TW": (886,),  # Taiwan (Province of China)
    "TJ": (992,),  # Tajikistan
    "TZ": (255,),  # Tanzania, United Republic of
    "TH": (66,),  # Thailand
    "TL": (),  # Timor-Leste
    "TG": (228,),  # Togo
    "TK": (690,),  # Tokelau
    "TO": (676,),  # Tonga
    "TT": (1868,),  # Trinidad and Tobago
    "TN": (216,),  # Tunisia
    "TR": (90,),  # Turkey
    "TM": (993,),  # Turkmenistan
    "TC": (1649,),  # Turks and Caicos Islands
    "TV": (668,),  # Tuvalu
    "UG": (256,),  # Uganda
    "UA": (380,),  # Ukraine
    "AE": (971,),  # United Arab Emirates
    "GB": (44,),  # United Kingdom of Great Britain and Northern Ireland
    "UM": (),  # United States Minor Outlying Islands
    "US": (1,),  # United States of America
    "UY": (598,),  # Uruguay
    "UZ": (998,),  # Uzbekistan
    "VU": (678,),  # Vanuatu
    "VE": (58,),  # Venezuela (Bolivarian Republic of)
    "VN": (84,),  # Viet Nam
    "VG": (),  # Virgin Islands (British)
    "VI": (),  # Virgin Islands (U.S.)
    "WF": (681,),  # Wallis and Futuna
    "EH": (),  # Western Sahara
    "XK": (383,),  # Kosovo
    "YE": (967,),  # Yemen
    "ZM": (260,),  # Zambia
    "ZW": (263,),  # Zimbabwe
}


def choices(codes: set = None, sort: bool = True) -> tuple:
    """
    Converts country codes into a list of code/name tuples suitable for choices on a form. If a set of country codes
    is not provided, we use the set of all countries.
    """
    cs = tuple((c, NAMES[c]) for c in codes) if codes else tuple((c, n) for c, n in NAMES.items())

    if sort:
        cs = sorted(cs, key=lambda x: x[1])
    return cs


def calling_codes(codes) -> set:
    """
    Converts country codes into a set of country codes used by those countries
    """
    cc = set()
    for code in codes:
        cc.update(CALLING_CODES.get(code, []))
    return cc


def from_tel(phone: str) -> Optional[str]:
    """
    Given a phone number in E164 returns the two letter country code for it.  ex: +250788383383 -> RW
    """
    try:
        parsed = phonenumbers.parse(phone)
        return phonenumbers.region_code_for_number(parsed)
    except Exception:
        return None
