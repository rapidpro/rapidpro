pip="${VENV}/bin/pip"

cd "${INSTALLDIR}/${REPO}/"

npm install -g coffee-script
npm install -g bower
bower install lessc

$pip install -r pip-freeze.txt
python manage.py syncdb
