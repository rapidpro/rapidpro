import re

from temba.templates.models import TemplateTranslation
from temba.utils.languages import alpha2_to_alpha3

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


def _extract_components(components) -> tuple:
    """
    Extracts components in our simplified format from payload of WhatsApp template components
    """

    extracted = []
    all_supported = True

    for component in components:
        comp_type = component["type"]
        comp_text = component.get("text", "")

        if comp_type == "HEADER":
            format = component.get("format", "TEXT")
            params = []

            if format == "TEXT":
                params = _extract_params(comp_text)
            elif format in ("IMAGE", "VIDEO", "DOCUMENT"):
                params = [{"type": format.lower()}]
            else:
                all_supported = False

            extracted.append({"type": "header", "content": comp_text, "params": params})

        elif comp_type == "BODY":
            params = _extract_params(comp_text)

            extracted.append({"type": "body", "content": comp_text, "params": params})

        elif comp_type == "FOOTER":
            extracted.append({"type": "footer", "content": comp_text, "params": []})

        elif comp_type == "BUTTONS":
            for button in component["buttons"]:
                button_text = button.get("text", "")

                if button["type"] == "QUICK_REPLY":
                    params = _extract_params(button_text)
                    extracted.append({"type": "button/quick_reply", "content": button_text, "params": params})

                elif button["type"] == "URL":
                    button_url = button.get("url", "")
                    params = _extract_params(button_text) + _extract_params(button_url)
                    extracted.append(
                        {"type": "button/url", "content": button_url, "display": button_text, "params": params}
                    )

                elif button["type"] == "PHONE_NUMBER":
                    phone_number = button.get("phone_number", "")
                    extracted.append(
                        {"type": "button/phone_number", "content": phone_number, "display": button_text, "params": []}
                    )

                else:
                    all_supported = False
        else:
            all_supported = False

    return extracted, all_supported


def update_local_templates(channel, wa_templates):
    channel_namespace = channel.config.get("fb_namespace", "")

    # run through all our templates making sure they are present in our DB
    seen = []
    for template in wa_templates:
        template_status = template["status"].upper()
        if template_status not in STATUS_MAPPING:  # ignore if this is a status we don't know about
            continue

        components, all_supported = _extract_components(template["components"])

        # TODO save components as a list... but for now organize them into a dict by type/index
        comps_as_dict = {}
        button_index = 0
        for comp in components:
            comp_type = comp["type"]
            if comp_type.startswith("button/"):
                comps_as_dict[f"button.{button_index}"] = comp
                button_index += 1
            else:
                comps_as_dict[comp_type] = comp

        status = STATUS_MAPPING[template_status]
        if not all_supported:
            status = TemplateTranslation.STATUS_UNSUPPORTED_COMPONENTS

        missing_external_id = f"{template['language']}/{template['name']}"
        translation = TemplateTranslation.get_or_create(
            channel,
            template["name"],
            locale=parse_language(template["language"]),
            status=status,
            external_locale=template["language"],
            external_id=template.get("id", missing_external_id[:64]),
            namespace=template.get("namespace", channel_namespace),
            components=comps_as_dict,
        )

        seen.append(translation)

    # trim any translations we didn't see
    TemplateTranslation.trim(channel, seen)


def parse_language(lang) -> str:
    """
    Converts a WhatsApp language code which can be alpha2 ('en') or alpha2_country ('en_US') or alpha3 ('fil')
    to our locale format ('eng' or 'eng-US').
    """
    language, country = lang.split("_") if "_" in lang else [lang, None]
    if len(language) == 2:
        language = alpha2_to_alpha3(language)

    return f"{language}-{country}" if country else language
