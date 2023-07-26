#!/usr/bin/env python3

import os
import sys

from hamlpy.compiler import Compiler

source = sys.argv[1]
haml_parser = Compiler(options={"attr_wrapper": '"', "smart_quotes": True, "endblock_names": True})


def convert_template(haml_path: str, delete: bool) -> str:
    html_path = os.path.splitext(haml_path)[0] + ".html"

    with open(haml_path, "r") as file:
        haml_content = file.read()

    html_content = haml_parser.process(haml_content)

    with open(html_path, "w") as file:
        file.write(html_content)

    if delete:
        os.remove(haml_path)

    return html_path


def convert_directory(path: str, delete: bool):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            if name.endswith(".haml"):
                haml_path = os.path.join(root, name)
                convert_template(haml_path, delete=delete)


if os.path.isdir(source):
    convert_directory(source, delete=True)
    converted = source
else:
    converted = convert_template(source, delete=True)

os.system(f"djlint --profile=django --reformat --quiet {converted}")
