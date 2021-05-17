import pycountry

from django.core.management.base import BaseCommand

from temba.orgs.models import Language, Org


class Command(BaseCommand):  # pragma: no cover
    help = "Utility to find non ISO-639-1 languages that need to be explicitly allowed"

    def handle(self, *args, **options):
        # get all distinct ISO-639-2/ISO-639-3 codes in use
        codes = (
            Language.objects.filter(org__is_active=True)
            .values_list("iso_code", flat=True)
            .distinct()
            .order_by("iso_code")
        )

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
        orgs = (
            Org.objects.filter(languages__iso_code__in=languages.keys())
            .prefetch_related("languages")
            .distinct()
            .order_by("name")
        )

        print("The following orgs currently use non-ISO-639-1 languages:\n")

        # print each org and the languages it uses
        for org in orgs:
            print(f"{org.name} (#{org.id})")
            for lang in org.languages.all():
                name, alpha_3, alpha_2 = "", lang.iso_code, ""
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
