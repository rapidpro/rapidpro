#!/usr/bin/env python3

import argparse
import subprocess

import colorama

parser = argparse.ArgumentParser(description="Code checks")
parser.add_argument("--skip-flake", action="store_true")
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

    status("Make any missing migrations")
    cmd("python manage.py makemigrations")

    status("Running black")
    cmd("black --line-length=119 temba")

    if not args.skip_flake:
        status("Running flake8")
        cmd("flake8")

    status("Running isort")
    cmd("isort -rc temba")

    # if any code changes were made, exit with error
    if cmd("git diff temba locale"):
        print("👎 " + colorama.Fore.RED + "Changes to be committed")
        exit(1)
    else:
        print("👍 " + colorama.Fore.GREEN + "Code looks good. Make that PR!")
