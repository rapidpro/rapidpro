#!/bin/bash

# cookbook
#
# add or remove a package
#   1. add/remove package from/to pip-requires.txt
#   2. run "./freeze-python-deps.sh"
#   3. run "pip-sync pip-freeze.txt"
#
# update a package
#   1. run "./freeze-python-deps.sh -P name_of_the_package"
#   2. run "pip-sync pip-freeze.txt"
#
# update a package to a specific version
#   1. run "./freeze-python-deps.sh -P name_of_the_package==1.3.6"
#   2. run "pip-sync pip-freeze.txt"
#

PIP_REQUIRES_FILE=pip-requires.txt
PIP_FREEZE_FILE=pip-freeze.txt

# these packages should always be at their most recent version
ROLLING_VERSION_PACKAGES="pytz phonenumbers"

# do the actual requirements file build, ignore caches/rebuild from scratch
CUSTOM_COMPILE_COMMAND="./freeze-python-deps.sh" pip-compile --annotate --rebuild --output-file $PIP_FREEZE_FILE $PIP_REQUIRES_FILE $@


for rolling_package in $ROLLING_VERSION_PACKAGES; do
    # On OSX, you need use the gnu-sed instead of the pre installed sed
    # brew install gnu-sed --with-default-names
    sed -i "s/$rolling_package==.*/$rolling_package/" $PIP_FREEZE_FILE

done
