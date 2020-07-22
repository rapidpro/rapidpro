#!/usr/bin/env python3

import argparse
import subprocess

import colorama
import polib

parser = argparse.ArgumentParser(description="Code checks")
parser.add_argument("--skip-compilemessages", action="store_true")
parser.add_argument("--debug", action="store_true")
args = parser.parse_args()

DEBUG = args.debug


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
    def get_current_msgids():
        pot = polib.pofile("locale/en_US/LC_MESSAGES/django.po")
        return {e.msgid for e in pot if not e.fuzzy and not e.obsolete}

    cmd(f"git restore --staged --worktree locale")

    # get the current set of msgids
    saved_msgids = get_current_msgids()

    # re-extract locale files from source code
    ignore_paths = ("env/*", "fabric/*", "media/*", "sitestatic/*", "static/*", "node_modules/*")
    ignore_args = " ".join([f'--ignore="{p}"' for p in ignore_paths])

    cmd(f"python manage.py makemessages -a -e haml,html,txt,py --no-location --no-wrap {ignore_args}")

    # get the new set of msgids
    actual_msgids = get_current_msgids()

    added_msgids = actual_msgids.difference(saved_msgids)
    removed_msgids = saved_msgids.difference(actual_msgids)

    if DEBUG:
        for mid in added_msgids:
            print(f"  + {repr(mid)}")
        for mid in removed_msgids:
            print(f"  - {repr(mid)}")

    # if there are no actual changes to msgids, revert
    if not added_msgids and not removed_msgids:
        cmd(f"git restore locale")


if __name__ == "__main__":
    colorama.init()

    status("Make any missing migrations")
    cmd("python manage.py makemigrations")

    status("Running black")
    cmd("black --line-length=119 --target-version=py36 temba")

    status("Running flake8")
    cmd("flake8")

    status("Running isort")
    cmd("isort -rc temba")

    status("Updating locale PO files")
    update_po_files()

    if not args.skip_compilemessages:
        status("Recompiling locale MO files")
        cmd("python manage.py compilemessages")

    # if any code changes were made, exit with error
    if cmd("git diff temba locale"):
        print("👎 " + colorama.Fore.RED + "Changes to be committed")
        exit(1)
    else:
        print("👍 " + colorama.Fore.GREEN + "Code looks good. Make that PR!")
