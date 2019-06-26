#!/bin/sh

# detect which files are actually going to be commited
FILES=$(git diff --name-only HEAD^ HEAD | grep -e '\.py$')

if [ -a .commit ]; then
    rm .commit

    git add $FILES
    git commit --amend -C HEAD --no-verify
fi

