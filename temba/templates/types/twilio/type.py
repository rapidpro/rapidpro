import base64
import re

import requests

from django.utils import timezone

from temba.channels.models import Channel
from temba.request_logs.models import HTTPLog

from ...models import TemplateTranslation, TemplateType


class TwilioType(TemplateType):
    slug = "twilio"
    variable_regex = re.compile(r"{{([A-Za-z0-9]+)}}")

    STATUS_MAPPING = {
        "PENDING": TemplateTranslation.STATUS_PENDING,
        "APPROVED": TemplateTranslation.STATUS_APPROVED,
        "REJECTED": TemplateTranslation.STATUS_REJECTED,
        "PAUSED": TemplateTranslation.STATUS_PAUSED,
        "DISABLED": TemplateTranslation.STATUS_DISABLED,
        "IN_APPEAL": TemplateTranslation.STATUS_IN_APPEAL,
    }

    def update_local(self, channel, raw: dict):
        credentials_base64 = base64.b64encode(
            f"{channel.config[Channel.CONFIG_ACCOUNT_SID]}:{channel.config[Channel.CONFIG_AUTH_TOKEN]}".encode()
        ).decode()

        headers = {"Authorization": f"Basic {credentials_base64}"}

        approval_url = raw["links"]["approval_fetch"]
        approval_start = timezone.now()
        try:
            response = requests.get(approval_url, headers=headers)
            response.raise_for_status()
            HTTPLog.from_response(
                HTTPLog.WHATSAPP_TEMPLATES_SYNCED, response, approval_start, timezone.now(), channel=channel
            )
            raw_status = response.json()["whatsapp"]["status"].upper()
            if raw_status not in self.STATUS_MAPPING:  # we handle statuses of DELETED or PENDING_DELETION by deleting
                return None

            status = self.STATUS_MAPPING[raw_status]
        except requests.RequestException as e:
            HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, approval_start, channel=channel)
            status = TemplateTranslation.STATUS_PENDING

        components, variables, supported = self._extract_types(raw["types"])

        return TemplateTranslation.get_or_create(
            channel,
            raw["friendly_name"],
            locale=self._parse_language(raw["language"]),
            status=status,
            external_locale=raw["language"],
            external_id=raw.get("sid"),
            namespace="",
            components=components,
            variables=variables,
            is_supported=supported,
        )

    def _extract_types(self, raw: list) -> tuple:
        """
        Extracts twilio types in our simplified format from payload of WhatsApp template components
        """
        components = []
        variables = []
        supported = True
        shared_map = {}

        def add_variables(names: list, typ: str) -> dict:
            map = {}
            for name in names:
                if name in shared_map:
                    map[name] = shared_map[name]
                else:
                    variables.append({"type": typ})
                    map[name] = len(variables) - 1
                    shared_map[name] = len(variables) - 1
            return map

        for content_type in raw:
            if raw[content_type].get("header_text"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["header_text"]), "text")
                components.append(
                    {
                        "name": "header",
                        "type": "header/text",
                        "content": raw[content_type]["header_text"],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("media"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["media"][0]), "text")
                components.append(
                    {
                        "name": "header",
                        "type": "header/media",
                        "content": raw[content_type]["media"][0],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("body"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["body"]), "text")

                components.append(
                    {
                        "name": "body",
                        "type": "body/text",
                        "content": raw[content_type]["body"],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("footer"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["footer"]), "text")
                components.append(
                    {
                        "name": "footer",
                        "type": "footer/text",
                        "content": raw[content_type]["footer"],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("title"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["title"]), "text")
                components.append(
                    {
                        "name": "body",
                        "type": "body/text",
                        "content": raw[content_type]["title"],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("subtitle"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["subtitle"]), "text")
                components.append(
                    {
                        "name": "footer",
                        "type": "footer/text",
                        "content": raw[content_type]["subtitle"],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("items") or raw[content_type].get("dynamic_items"):
                supported = False

            if raw[content_type].get("actions"):

                for idx, action in enumerate(raw[content_type]["actions"]):
                    button_name = f"button.{idx}"
                    button_text = action.get("title", "")

                    if content_type == "twilio/quick-reply":
                        button_vars = add_variables(self._extract_variables(button_text), "text")
                        components.append(
                            {
                                "name": button_name,
                                "type": "button/quick_reply",
                                "content": button_text,
                                "variables": button_vars,
                            }
                        )
                    elif content_type in ["twilio/call-to-action", "twilio/card", "whatsapp/card"]:
                        button_type = action["type"]
                        if button_type == "URL":
                            button_url = action["url"]
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
                            phone_number = action["phone"]
                            components.append(
                                {
                                    "name": button_name,
                                    "type": "button/phone_number",
                                    "content": phone_number,
                                    "display": button_text,
                                    "variables": {},
                                }
                            )
                        elif button_type == "QUICK_REPLY":
                            button_vars = add_variables(self._extract_variables(button_text), "text")
                            components.append(
                                {
                                    "name": button_name,
                                    "type": "button/quick_reply",
                                    "content": button_text,
                                    "variables": button_vars,
                                }
                            )
                        else:
                            supported = False
                    else:
                        supported = False

        return components, variables, supported and (len(components) > 0)
