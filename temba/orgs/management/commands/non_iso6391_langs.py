import pycountry

from django.core.management.base import BaseCommand

from temba.orgs.models import Org


class Command(BaseCommand):  # pragma: no cover
    help = "Utility to find non ISO-639-1 languages that need to be explicitly allowed"

    def handle(self, *args, **options):
        # get all distinct ISO-639-2/ISO-639-3 codes in use
        codes = set()
        for org in Org.objects.filter(is_active=True):
            codes.update(org.flow_languages)
        codes = sorted(codes)

        # find all languages which aren't in ISO-639-1
        languages = {}
        for code in codes:
            name, alpha_2 = "", ""
            language = pycountry.languages.get(alpha_3=code)
            if language:
                name = language.name
                alpha_2 = getattr(language, "alpha_2", "")
            if not alpha_2 or not name:
                languages[code] = name

        # fetch all orgs using one of these languages
        orgs = Org.objects.filter(flow_languages__overlap=list(languages.keys())).order_by("name")

        print("The following orgs currently use non-ISO-639-1 languages:\n")

        # print each org and the languages it uses
        for org in orgs:
            print(f"{org.name} (#{org.id})")
            for alpha_3 in org.flow_languages:
                name, alpha_2 = "", ""
                language = pycountry.languages.get(alpha_3=alpha_3)
                if language:
                    name = language.name
                    alpha_2 = getattr(language, "alpha_2", "--")

                print(f"  * {alpha_3} {alpha_2} {name}")

        print("\n")
        print("Add this Django setting to continue to allow orgs to use those languages:\n")

        # print setting to use to allow these languages
        print("NON_ISO6391_LANGUAGES = {")
        for code, name in languages.items():
            print(f'    "{code}",  # {name}')
        print("}")
