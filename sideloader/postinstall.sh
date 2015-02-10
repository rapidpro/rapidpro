pip="${VENV}/bin/pip"

cd "${INSTALLDIR}/${REPO}/"

$pip install -r pip-freeze.txt
