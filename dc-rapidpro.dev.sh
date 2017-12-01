#!/bin/bash
if [ ! -d "env" ]; then
  echo Virtual Environment not detected, initializing now.
  virtualenv env
  env/bin/python -m pip install -r pip-requires.txt
  env/bin/python -m pip install -r pip-freeze.txt
fi
/usr/local/bin/docker-compose -f ./rapidpro-compose.dev.yaml -p rapidpro "$@"