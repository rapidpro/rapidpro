import re

from ...models import TemplateTranslation, TemplateType


class WhatsAppType(TemplateType):
    slug = "whatsapp"
    variable_regex = re.compile(r"{{(\d+)}}")

    STATUS_MAPPING = {
        "PENDING": TemplateTranslation.STATUS_PENDING,
        "APPROVED": TemplateTranslation.STATUS_APPROVED,
        "REJECTED": TemplateTranslation.STATUS_REJECTED,
    }

    def update_local(self, channel, raw: dict):
        channel_namespace = channel.config.get("fb_namespace", "")

        template_status = raw["status"].upper()
        if template_status not in self.STATUS_MAPPING:  # ignore if this is a status we don't know about
            return None

        components, variables, supported = self._extract_components(raw["components"])

        status = self.STATUS_MAPPING[template_status]
        if not supported:
            status = TemplateTranslation.STATUS_UNSUPPORTED

        missing_external_id = f"{raw['language']}/{raw['name']}"
        return TemplateTranslation.get_or_create(
            channel,
            raw["name"],
            locale=self._parse_language(raw["language"]),
            status=status,
            external_locale=raw["language"],
            external_id=raw.get("id", missing_external_id[:64]),
            namespace=raw.get("namespace", channel_namespace),
            components=components,
            variables=variables,
        )

    def _extract_components(self, raw: list) -> tuple:
        """
        Extracts components in our simplified format from payload of WhatsApp template components
        """

        components = []
        variables = []
        supported = True

        def add_variables(names: list, typ: str) -> dict:
            map = {}
            for name in names:
                variables.append({"type": typ})
                map[name] = len(variables) - 1
            return map

        for component in raw:
            comp_type = component["type"].upper()
            comp_text = component.get("text", "")

            if comp_type == "HEADER":
                comp_vars = {}

                if component.get("format", "TEXT") == "TEXT":
                    comp_vars = add_variables(self._extract_variables(comp_text), "text")
                else:
                    supported = False

                components.append({"type": "header", "name": "header", "content": comp_text, "variables": comp_vars})

            elif comp_type == "BODY":
                comp_vars = add_variables(self._extract_variables(comp_text), "text")

                components.append({"type": "body", "name": "body", "content": comp_text, "variables": comp_vars})

            elif comp_type == "FOOTER":
                components.append({"type": "footer", "name": "footer", "content": comp_text, "variables": {}})

            elif comp_type == "BUTTONS":
                for idx, button in enumerate(component["buttons"]):
                    button_type = button["type"].upper()
                    button_name = f"button.{idx}"
                    button_text = button.get("text", "")

                    if button_type == "QUICK_REPLY":
                        button_vars = add_variables(self._extract_variables(button_text), "text")
                        components.append(
                            {
                                "type": "button/quick_reply",
                                "name": button_name,
                                "content": button_text,
                                "variables": button_vars,
                            }
                        )

                    elif button_type == "URL":
                        button_url = button.get("url", "")
                        button_vars = add_variables(self._extract_variables(button_url), "text")
                        components.append(
                            {
                                "type": "button/url",
                                "name": button_name,
                                "content": button_url,
                                "display": button_text,
                                "variables": button_vars,
                            }
                        )

                    elif button_type == "PHONE_NUMBER":
                        phone_number = button.get("phone_number", "")
                        components.append(
                            {
                                "type": "button/phone_number",
                                "name": button_name,
                                "content": phone_number,
                                "display": button_text,
                                "variables": {},
                            }
                        )

                    else:
                        supported = False
            else:
                supported = False

        return components, variables, supported
