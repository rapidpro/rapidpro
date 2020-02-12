#!/usr/bin/env python3

import os
import subprocess

import colorama

DEBUG = "TRAVIS" in os.environ


def cmd(line):
    if DEBUG:
        print(colorama.Style.DIM + "% " + line + colorama.Style.RESET_ALL)
    try:
        output = subprocess.check_output(line, shell=True).decode("utf-8")
        if DEBUG:
            print(colorama.Style.DIM + output + colorama.Style.RESET_ALL)
        return output
    except subprocess.CalledProcessError as e:
        print(colorama.Fore.RED + e.stdout.decode("utf-8") + colorama.Style.RESET_ALL)
        exit(1)


def status(line):
    print(colorama.Fore.GREEN + f">>> {line}..." + colorama.Style.RESET_ALL)


def update_po_files():
    ignore_paths = ("env/*", "static/bower/*", "static/components/*", "node_modules/*")
    ignore_args = " ".join([f'--ignore="{p}"' for p in ignore_paths])

    cmd(f"python manage.py makemessages -a -e haml,html,txt,py --no-location --no-wrap {ignore_args}")
    cmd(f"python manage.py makemessages -d djangojs -a --no-location --no-wrap {ignore_args}")

    modified_pos = cmd("git diff --name-only locale/").split("\n")
    for po in modified_pos:
        # we only care about changes to msgids, so if we can't find any of those, revert the file
        if not cmd(rf'git diff -U0 {po} | grep -e "^[\+\-]msgid" || true').strip():
            cmd(f"git checkout -- {po}")


if __name__ == "__main__":
    colorama.init()

    status("Running black")
    cmd("black --line-length=119 --target-version=py36 temba")
    status("Running flake8")
    cmd("flake8")
    status("Running isort")
    cmd("isort -rc temba")
    status("Updating locale PO files")
    update_po_files()
    status("Recompiling locale MO files")
    cmd("python manage.py compilemessages")
    status("Make any missing migrations")
    cmd("python manage.py makemigrations")

    # if any code changes were made, exit with error
    if cmd("git diff temba locale"):
        print("👎 " + colorama.Fore.RED + "Changes to be committed")
        exit(1)
    else:
        print("👍 " + colorama.Fore.GREEN + "Code looks good. Make that PR!")
