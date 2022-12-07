#!/bin/sh

# auto check for pep8 so we don't check in bad code
FILES=$(git diff --cached --name-only --diff-filter=ACM | grep -e '\.py$')

if [ -n "$FILES" ]; then
    if [ $(isort $FILES | wc -l) -ne 0 ]; then
        isort -q $FILES
        touch .commit
    fi
fi

if [ -n "$FILES" ]; then
    if black $FILES; then
        touch .commit
    fi
fi

if [ -n "$FILES" ]; then
    ruff_errors=$(ruff temba --exit-zero $FILES)

    if [ ! -z "$ruff_errors" ]; then
        rm -f .commit
        echo "$ruff_errors"
        exit 1
    fi
fi
