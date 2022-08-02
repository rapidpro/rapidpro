import subprocess
import time

import requests

from django.conf import settings
from django.core.management import BaseCommand


class Command(BaseCommand):
    help = "Updates the default translation and fetches other translations from Transifex"

    def add_arguments(self, parser):
        parser.add_argument("--token", type=str, action="store", dest="token", required=True)

    def handle(self, token, *args, **kwargs):
        self.client = TransifexClient(token)

        for lang, name in settings.LANGUAGES:
            if lang != settings.DEFAULT_LANGUAGE:
                self.fetch_translation(lang)

        self.stdout.write("running makemessages to extract new strings...")

        self.extract_default_translation()

        self.stdout.write("compiling MO files...")

        # rebuild the .mo files too
        subprocess.check_output("./manage.py compilemessages", shell=True)

        self.stdout.write("🍾 finished")

    def extract_default_translation(self):
        """
        Extracts the default translation using makemessages
        """
        ignore_paths = ("env/*", ".venv/*", "fabric/*", "media/*", "sitestatic/*", "static/*", "node_modules/*")
        ignore_args = " ".join([f'--ignore="{p}"' for p in ignore_paths])
        cmd = f"python manage.py makemessages -a -e haml,html,txt,py --no-location --no-wrap {ignore_args}"
        subprocess.check_output(cmd, shell=True)

    def fetch_translation(self, lang: str):
        self.stdout.write(f"fetching translation for {lang}..")

        # convert lang code to underscore format (e.g. pt-br > pt_BR)
        lang = self.convert_lang_code(lang)

        response = self.client.create_translation_download("rapidpro", "rapidpro", "django-po--main", lang)
        download_id = response.json()["data"]["id"]

        while True:
            time.sleep(1)
            self.stdout.write(" > checking for download...")
            response = self.client.check_download_status(download_id)

            if response.status_code == 303:
                self.download_translation(lang, response.headers["Location"])
                break

    def download_translation(self, lang: str, url: str):
        self.stdout.write(" > downloading translation...")

        po_path = f"locale/{lang}/LC_MESSAGES/django.po"
        response = requests.get(url)

        with open(po_path, "wb") as dest:
            dest.write(response.content)

        self.stdout.write(f" > {po_path} updated")

    def convert_lang_code(self, lang: str) -> str:
        parts = lang.split("-")
        if len(parts) > 1:
            parts[1] = parts[1].upper()
        return "_".join(parts)


class TransifexClient:
    def __init__(self, token: str):
        self.base_url = "https://rest.api.transifex.com/"
        self.token = token

    def create_translation_download(self, org: str, project: str, resource: str, lang: str):
        return self._post(
            "resource_translations_async_downloads",
            {
                "data": {
                    "attributes": {
                        "content_encoding": "text",
                        "file_type": "default",
                        "mode": "default",
                        "pseudo": False,
                    },
                    "relationships": {
                        "language": {"data": {"id": f"l:{lang}", "type": "languages"}},
                        "resource": {"data": {"id": f"o:{org}:p:{project}:r:{resource}", "type": "resources"}},
                    },
                    "type": "resource_translations_async_downloads",
                }
            },
        )

    def check_download_status(self, download_id):
        return self._get(f"resource_translations_async_downloads/{download_id}")

    def _get(self, url):
        return requests.get(
            self.base_url + url, headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=False
        )

    def _post(self, url, data):
        return requests.post(
            self.base_url + url,
            json=data,
            headers={"Content-Type": "application/vnd.api+json", "Authorization": f"Bearer {self.token}"},
        )
