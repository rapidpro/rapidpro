import re

from ...models import TemplateTranslation, TemplateType


class WhatsAppType(TemplateType):
    slug = "whatsapp"
    variable_regex = re.compile(r"{{(\d+)}}")

    STATUS_MAPPING = {
        "PENDING": TemplateTranslation.STATUS_PENDING,
        "APPROVED": TemplateTranslation.STATUS_APPROVED,
        "REJECTED": TemplateTranslation.STATUS_REJECTED,
        "PAUSED": TemplateTranslation.STATUS_PAUSED,
        "DISABLED": TemplateTranslation.STATUS_DISABLED,
        "IN_APPEAL": TemplateTranslation.STATUS_IN_APPEAL,
    }

    def update_local(self, channel, raw: dict):
        channel_namespace = channel.config.get("fb_namespace", "")

        raw_status = raw["status"].upper()
        if raw_status not in self.STATUS_MAPPING:  # we handle statuses of DELETED or PENDING_DELETION by deleting
            return None

        status = self.STATUS_MAPPING[raw_status]
        components, variables, supported = self._extract_components(raw["components"])

        return TemplateTranslation.get_or_create(
            channel,
            raw["name"],
            locale=self._parse_language(raw["language"]),
            status=status,
            external_locale=raw["language"],
            external_id=raw.get("id"),
            namespace=raw.get("namespace", channel_namespace),
            components=components,
            variables=variables,
            is_supported=supported,
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
                header_fmt = component.get("format", "TEXT")

                if header_fmt == "TEXT":
                    comp_type = "header/text"
                    comp_vars = add_variables(self._extract_variables(comp_text), "text")
                elif header_fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
                    comp_type = "header/media"
                    comp_vars = add_variables("1", header_fmt.lower())
                else:
                    comp_type = "header/unknown"
                    supported = False

                components.append({"name": "header", "type": comp_type, "content": comp_text, "variables": comp_vars})

            elif comp_type == "BODY":
                comp_vars = add_variables(self._extract_variables(comp_text), "text")

                components.append({"name": "body", "type": "body/text", "content": comp_text, "variables": comp_vars})

            elif comp_type == "FOOTER":
                components.append({"name": "footer", "type": "footer/text", "content": comp_text, "variables": {}})

            elif comp_type == "BUTTONS":
                for idx, button in enumerate(component["buttons"]):
                    button_type = button["type"].upper()
                    button_name = f"button.{idx}"
                    button_text = button.get("text", "")

                    if button_type == "QUICK_REPLY":
                        button_vars = add_variables(self._extract_variables(button_text), "text")
                        components.append(
                            {
                                "name": button_name,
                                "type": "button/quick_reply",
                                "content": button_text,
                                "variables": button_vars,
                            }
                        )

                    elif button_type == "URL":
                        button_url = button.get("url", "")
                        button_vars = add_variables(self._extract_variables(button_url), "text")
                        components.append(
                            {
                                "name": button_name,
                                "type": "button/url",
                                "content": button_url,
                                "display": button_text,
                                "variables": button_vars,
                            }
                        )

                    elif button_type == "PHONE_NUMBER":
                        phone_number = button.get("phone_number", "")
                        components.append(
                            {
                                "name": button_name,
                                "type": "button/phone_number",
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
