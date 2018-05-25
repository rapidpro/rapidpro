#!/bin/sh
echo
if [ -a .commit ]
    then
	rm .commit
	git add .
        git commit --amend -C HEAD --no-verify
fi
exit
