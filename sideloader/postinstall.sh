pip="${VENV}/bin/pip"

cd "${INSTALLDIR}/${REPO}/"

if [ ! -f `which coffee` ]; then
    npm install -g coffee-script
fi

if [ ! -f `which bower` ]; then
    npm install -g bower
fi

if [ ! -f `which lessc` ]; then
    bower --allow-root install lessc
fi

$pip install -r pip-freeze.txt
PYTHONPATH=`pwd` python manage.py syncdb
