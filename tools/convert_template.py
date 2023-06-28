#!/usr/bin/env python3

import os
import re
import sys

from hamlpy.compiler import Compiler

source = sys.argv[1]
haml_parser = Compiler(options={"attr_wrapper": '"', "smart_quotes": True, "endblock_names": True})

sad_template = re.compile("\\{\n\\s+(\\{|%)")
sad_files = {}


def check_for_sad(path: str):
    # check for sad templates
    formatted = open(path, "r")
    formatted_text = formatted.read()
    formatted.close()
    matches = sad_template.findall(formatted_text)
    if matches:
        sad_files[path] = len(matches)


def format_path(path: str, *, delete: bool):
    os.system(f"djlint --profile=django --reformat --quiet {path}")

    if os.path.isdir(path):
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                if name.endswith(".html"):
                    filename = os.path.join(root, name)
                    check_for_sad(filename)
                    if delete and filename not in sad_files:
                        haml_file = filename.replace(".html", ".haml")
                        if os.path.exists(haml_file):
                            os.remove(filename.replace(".html", ".haml"))
    else:
        check_for_sad(path)
        if delete and path not in sad_files:
            os.remove(path)


def convert_template(haml_path: str):
    html_path = os.path.splitext(haml_path)[0] + ".html"

    with open(haml_path, "r") as file:
        haml_content = file.read()

    html_content = haml_parser.process(haml_content)

    with open(html_path, "w") as file:
        file.write(html_content)


def convert_directory(path: str):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            if name.endswith(".haml"):
                convert_template(os.path.join(root, name))


def print_sad():
    for f in sad_files.keys():
        print(f"😔 {f}: {sad_files[f]}")


if os.path.isdir(source):
    convert_directory(source)
    format_path(source, delete=True)
    print_sad()
else:
    convert_template(source)
    format_path(source.replace(".haml", ".html"), delete=True)
    print_sad()
