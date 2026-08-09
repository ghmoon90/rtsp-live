python -m pip install --upgrade pip
pip freeze > req_freeze.txt
pip uninstall -r req_freeze.txt -y
del req_freeze.txt
pip install -r requirements.txt