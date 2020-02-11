#!/usr/bin/env python3

import re
import subprocess

import colorama


def cmd(line):
    try:
        return subprocess.check_output(line, shell=True).decode("utf-8")
    except subprocess.CalledProcessError as e:
        print(colorama.Fore.RED + e.stdout.decode("utf-8"))
        exit(1)


def status(line):
    print(colorama.Fore.GREEN + f">>> {line}..." + colorama.Style.RESET_ALL)


def update_po_files():
    numstat_output_regex = re.compile(r"^(?P<added>\d+)\s+(?P<deleted>\d+)\s+(?P<path>\S+)$")

    ignore_paths = ("env/*", "static/bower/*", "static/components/*", "node_modules/*")
    ignore_args = " ".join(['--ignore="%s"' % p for p in ignore_paths])

    cmd("python manage.py makemessages -a -e haml,html,txt,py --no-location --no-wrap %s" % ignore_args)
    cmd("python manage.py makemessages -d djangojs -a --no-location --no-wrap %s" % ignore_args)

    modified_pos = cmd("git diff --name-only locale/").split("\n")
    for po in modified_pos:
        output = cmd("git diff --numstat %s" % po).strip()
        match = numstat_output_regex.match(output)
        if match:
            # if one or less lines have changed, then it's only the POT-Creation-Date header, which isn't significant
            # so undo that change to make a simpler diff
            if int(match.group("added")) <= 1 and int(match.group("deleted")) <= 1:
                cmd("git checkout -- %s" % po)


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
