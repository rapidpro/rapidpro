import pycountry

"""
These are exceptions according to ISO 4217 because they are super-national
currencies, not belonging to any single country.
"""
CURRENCY_EXCEPTIONS = {
    'AD': 'EUR',
    'AT': 'EUR',
    'AX': 'EUR',
    'BE': 'EUR',
    'BL': 'EUR',
    'CY': 'EUR',
    'DE': 'EUR',
    'EE': 'EUR',
    'ES': 'EUR',
    'FI': 'EUR',
    'FR': 'EUR',
    'GF': 'EUR',
    'GP': 'EUR',
    'GR': 'EUR',
    'IE': 'EUR',
    'IT': 'EUR',
    'LT': 'EUR',
    'LU': 'EUR',
    'LV': 'EUR',
    'MC': 'EUR',
    'ME': 'EUR',
    'MF': 'EUR',
    'MQ': 'EUR',
    'MT': 'EUR',
    'NL': 'EUR',
    'PM': 'EUR',
    'PT': 'EUR',
    'RE': 'EUR',
    'SI': 'EUR',
    'SK': 'EUR',
    'SM': 'EUR',
    'VA': 'EUR',
    'AS': 'USD',
    'BQ': 'USD',
    'EC': 'USD',
    'FM': 'USD',
    'GU': 'USD',
    'HT': 'USD',
    'MH': 'USD',
    'MP': 'USD',
    'PA': 'USD',
    'PR': 'USD',
    'PW': 'USD',
    'SV': 'USD',
    'TC': 'USD',
    'TL': 'USD',
    'US': 'USD',
    'VG': 'USD',
    'VI': 'USD',
}


def currency_for_country(alpha2):
    country = pycountry.countries.get(alpha2=str(alpha2))
    try:
        currency = pycountry.currencies.get(numeric=country.numeric)
        return currency.letter
    except:
        return CURRENCY_EXCEPTIONS.get(str(alpha2))
