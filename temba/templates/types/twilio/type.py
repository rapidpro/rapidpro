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
        "UNSUBMITTED": TemplateTranslation.STATUS_PENDING,
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
            template_status = response.json()["whatsapp"]["status"]
        except Exception as e:
            HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, approval_start, channel=channel)
            template_status = "unsubmitted"

        template_status = template_status.upper()
        if template_status not in self.STATUS_MAPPING:  # ignore if this is a status we don't know about
            return None

        components, variables, supported = self._extract_types(raw["types"])

        status = self.STATUS_MAPPING[template_status]
        if not supported:
            status = TemplateTranslation.STATUS_UNSUPPORTED

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
            if raw[content_type].get("body"):
                comp_vars = add_variables(self._extract_variables(raw[content_type]["body"]), "text")

                components.append(
                    {
                        "type": "body",
                        "name": "body",
                        "content": raw[content_type]["body"],
                        "variables": comp_vars,
                    }
                )

            if raw[content_type].get("media"):
                if self._extract_variables(raw[content_type]["media"][0]):
                    supported = False

            if raw[content_type].get("actions"):

                for idx, action in enumerate(raw[content_type]["actions"]):
                    button_name = f"button.{idx}"
                    button_text = action.get("title", "")

                    if content_type == "twilio/quick-reply":
                        button_vars = add_variables(self._extract_variables(button_text), "text")
                        components.append(
                            {
                                "type": "button/quick_reply",
                                "name": button_name,
                                "content": button_text,
                                "variables": button_vars,
                            }
                        )
                    elif content_type == "twilio/call-to-action":
                        button_type = action["type"]
                        if button_type == "URL":
                            button_url = action["url"]
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
                            phone_number = action["phone"]
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

        return components, variables, supported and (len(components) > 0)
