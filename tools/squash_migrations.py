#!/usr/bin/env python3

# Script to squash RapidPro migrations in two steps.
#
# Usage: `./tools/squash_migrations.py <step>`
#
# Steps:
#
# 1. Generate empty versions of new squashed migrations.
# 2. Replace empty versions with real squashed migrations and delete originals
#
# Both steps must be run on an empty database or at least an empty django_migrations table.
#
# Known issues:
#
# 1. Final squashed migration in sql app has broken import
# 2. Leaves behind .sql files used by deleted migrations in sql app
# 3. Need to manually add HStoreExtension operation in msgs and campaigns squashed migrations

import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from typing import Callable, List

import colorama

MIGRATION_FILENAME_REGEX = re.compile(r"\d{4}_\w+\.py")
EMPTY_MIGRATION = """# This is a dummy migration which will be implemented in the next release

{{IMPORTS}}


class Migration(migrations.Migration):

    dependencies = [{{DEPS}}]

    operations = []
"""


def get_app_names(exclude: List[str]) -> List[str]:
    """
    Gets names of all apps using migrations
    """
    names = []
    for app_dir in os.scandir("temba"):
        if app_dir.is_dir() and app_dir.name not in exclude:
            mig_dir = os.path.join("temba", app_dir.name, "migrations")
            if os.path.exists(mig_dir):
                names.append(app_dir.name)
    return sorted(names)


def get_app_migration_modules(app_name: str) -> List[str]:
    """
    Gets module names of all migration files for the given app
    """
    mods = []
    mig_dir = os.path.join("temba", app_name, "migrations")
    for mig_file in os.scandir(mig_dir):
        if MIGRATION_FILENAME_REGEX.match(mig_file.name):
            mods.append(mig_file.name[:-3])
    return sorted(mods)


def cmd(line: str):
    print(colorama.Style.DIM + "% " + line + colorama.Style.RESET_ALL)
    try:
        subprocess.check_output(line, shell=True).decode("utf-8")
    except subprocess.CalledProcessError as e:
        print(colorama.Fore.RED + e.stdout.decode("utf-8") + colorama.Style.RESET_ALL)
        exit(1)


def rewrite_file(path: str, transform: Callable[[str], str]) -> bool:
    with open(path, "r") as f:
        data = f.read()

    new_data = transform(data)

    with open(path, "w") as f:
        f.write(new_data)

    return data != new_data


def squash_migrations(step: int):
    app_names = get_app_names(exclude=["auth_tweaks"])

    app_original_migs = defaultdict(list)

    # for each app, disable existing migrations by temporarily renaming operations list to __operations
    for app_name in app_names:
        mig_mods = get_app_migration_modules(app_name)

        for mig_mod in mig_mods:
            mig_path = f"temba/{app_name}/migrations/{mig_mod}.py"

            with open(mig_path, "r") as f:
                is_empty = f.read().startswith("# This is a dummy migration")

            if is_empty:
                os.remove(mig_path)

                print(f"Removed empty dummy migration {mig_path}")
            else:
                app_original_migs[app_name].append(mig_mod)

                def transform(data):
                    return re.sub(r"operations = \[", "__operations = [", data, flags=re.DOTALL)

                rewrite_file(mig_path, transform)

                print(f"Disabled original migration {mig_path}")

    # generate replacement migrations for all apps
    cmd("python manage.py makemigrations --name squashed " + " ".join(app_names))

    # add an empty migration for the special sql app that has no models
    cmd("python manage.py makemigrations --name squashed --empty sql")

    for app_name in app_names:
        mig_mods = get_app_migration_modules(app_name)
        original_mods = app_original_migs[app_name]
        last_removed_dep = None

        for mig_mod in mig_mods:
            mig_path = f"temba/{app_name}/migrations/{mig_mod}.py"
            is_old_mig = mig_mod in original_mods

            if step == 1:
                if is_old_mig:
                    # re-enable migration
                    def transform(data):
                        return re.sub(r"__operations = \[", "operations = [", data, flags=re.DOTALL)

                    rewrite_file(mig_path, transform)

                    print(f"Re-enabled original migration {mig_path}")
                else:

                    def transform(data):
                        deps = re.search(r"dependencies = \[([^\]]*)\]", data, flags=re.DOTALL).group(1)

                        if "settings.AUTH_USER_MODEL" in data:
                            imports = "from django.conf import settings\nfrom django.db import migrations"
                        else:
                            imports = "from django.db import migrations"

                        return EMPTY_MIGRATION.replace("{{IMPORTS}}", imports).replace("{{DEPS}}", deps)

                    rewrite_file(mig_path, transform)

                    print(f"Emptied new migration {mig_path}")

            elif step == 2:
                if is_old_mig:
                    os.remove(mig_path)

                    last_removed_dep = f"('{app_name}', '{mig_mod}')"

                    print(f"Removed original migration {mig_path}")
                else:

                    def transform(data):
                        lines = data.splitlines(keepends=True)
                        new_lines = []
                        for line in lines:
                            # makemigrations puts dependencies on new lines so we just have to remove any line
                            # containing a dependency on the last original migration that was deleted
                            if last_removed_dep in line:
                                continue
                            new_lines.append(line)

                        return "".join(new_lines)

                    if rewrite_file(mig_path, transform):
                        print(f"Remove dependency on deleted migration in {mig_path}")

    if step == 2:
        # we need to build the last SQL app migration
        last_mig = get_app_migration_modules("sql")[-1]
        mig_path = f"temba/sql/migrations/{last_mig}.py"
        mig_num = last_mig[:4]
        shutil.copy2("temba/sql/current_functions.sql", f"temba/sql/migrations/{mig_num}_functions.sql")
        shutil.copy2("temba/sql/current_indexes.sql", f"temba/sql/migrations/{mig_num}_indexes.sql")
        shutil.copy2("temba/sql/current_triggers.sql", f"temba/sql/migrations/{mig_num}_triggers.sql")
        ops = f'InstallSQL("{mig_num}_functions"), InstallSQL("{mig_num}_indexes"), InstallSQL("{mig_num}_triggers")'

        def transform(data):
            return re.sub(r"operations = \[.*\]", f"operations = [{ops}]", data, flags=re.DOTALL)

        rewrite_file(mig_path, transform)


if __name__ == "__main__":
    colorama.init()
    step = int(sys.argv[1])

    squash_migrations(step)
