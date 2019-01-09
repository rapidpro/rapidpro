#!/bin/bash

PIP_REQUIRES_FILE=pip-requires.txt
PIP_FREEZE_FILE=pip-freeze.txt

# these packages should always be at their most recent version
ROLLING_VERSION_PACKAGES="pytz phonenumbers"

# do the actual requirements file build, ignore caches/rebuild from scratch
CUSTOM_COMPILE_COMMAND="./do-freeze-python-deps.bash" pip-compile --annotate --rebuild --output-file $PIP_FREEZE_FILE $PIP_REQUIRES_FILE


for rolling_package in $ROLLING_VERSION_PACKAGES; do

    sed -i "s/$rolling_package==.*/$rolling_package/" $PIP_FREEZE_FILE

done
