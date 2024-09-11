#!/usr/bin/env python3

import argparse
import subprocess

import colorama

parser = argparse.ArgumentParser(description="Code checks")
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


if __name__ == "__main__":
    colorama.init()

    status("Check for missing migrations")
    cmd("python manage.py makemigrations --check")

    status("Running isort")
    cmd("isort --check temba")

    status("Running black")
    cmd("black --check temba")

    status("Running ruff")
    cmd("ruff check temba")

    print("👍 " + colorama.Fore.GREEN + "Code looks good. Make that PR!")
