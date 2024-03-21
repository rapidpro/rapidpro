import re

from temba.utils.languages import alpha2_to_alpha3

from .models import TemplateTranslation

VARIABLE_RE = re.compile(r"{{(\d+)}}")

STATUS_MAPPING = {
    "PENDING": TemplateTranslation.STATUS_PENDING,
    "APPROVED": TemplateTranslation.STATUS_APPROVED,
    "REJECTED": TemplateTranslation.STATUS_REJECTED,
}


def _extract_params(content, _type="text") -> list:
    """
    Creates a parameter for each variable placeholder in the given content
    """
    params = []
    seen = set()

    for match in VARIABLE_RE.findall(content):
        if match not in seen:
            params.append({"type": _type})
            seen.add(match)

    return params


def extract_components(components) -> tuple:
    """
    Extracts components in our simplified format from payload of WhatsApp template components
    """

    extracted = []
    all_supported = True

    for component in components:
        comp_type = component["type"].upper()
        comp_text = component.get("text", "")

        if comp_type == "HEADER":
            format = component.get("format", "TEXT")
            params = []

            if format == "TEXT":
                params = _extract_params(comp_text)
            else:
                all_supported = False

            extracted.append({"type": "header", "name": "header", "content": comp_text, "params": params})

        elif comp_type == "BODY":
            params = _extract_params(comp_text)

            extracted.append({"type": "body", "name": "body", "content": comp_text, "params": params})

        elif comp_type == "FOOTER":
            extracted.append({"type": "footer", "name": "footer", "content": comp_text, "params": []})

        elif comp_type == "BUTTONS":
            for idx, button in enumerate(component["buttons"]):
                button_type = button["type"].upper()
                button_name = f"button.{idx}"
                button_text = button.get("text", "")

                if button_type == "QUICK_REPLY":
                    extracted.append(
                        {
                            "type": "button/quick_reply",
                            "name": button_name,
                            "content": button_text,
                            "params": _extract_params(button_text),
                        }
                    )

                elif button_type == "URL":
                    button_url = button.get("url", "")
                    extracted.append(
                        {
                            "type": "button/url",
                            "name": button_name,
                            "content": button_url,
                            "display": button_text,
                            "params": _extract_params(button_url),
                        }
                    )

                elif button_type == "PHONE_NUMBER":
                    phone_number = button.get("phone_number", "")
                    extracted.append(
                        {
                            "type": "button/phone_number",
                            "name": button_name,
                            "content": phone_number,
                            "display": button_text,
                            "params": [],
                        }
                    )

                else:
                    all_supported = False
        else:
            all_supported = False

    return extracted, all_supported


def parse_language(lang) -> str:
    """
    Converts a WhatsApp language code which can be alpha2 ('en') or alpha2_country ('en_US') or alpha3 ('fil')
    to our locale format ('eng' or 'eng-US').
    """
    language, country = lang.split("_") if "_" in lang else [lang, None]
    if len(language) == 2:
        language = alpha2_to_alpha3(language)

    return f"{language}-{country}" if country else language
