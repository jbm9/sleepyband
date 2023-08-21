init:
	pip install -r requirements.txt

test:
#	python3 -m unittest discover -s tests/ test_sleepyband.py
	python3 test_sleepyband.py

coverage:
	coverage run --branch test_sleepyband.py
	coverage html

codestyle:
	pycodestyle . --exclude=".#*" --max-line-length=120

.PHONY: init test coverage codestyle
