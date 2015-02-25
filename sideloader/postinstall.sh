pip="${VENV}/bin/pip"

cd "${INSTALLDIR}/${REPO}/"

if [ ! -f /usr/local/bin/coffee ]; then
    npm install -g coffee-script
fi

if [ ! -f /usr/bin/lessc ]; then
    npm install -g less
fi

$pip install -r pip-freeze.txt
PYTHONPATH=`pwd` python manage.py syncdb
