init:
	pip install -r requirements.txt

test:
	python3 -m unittest discover -s tests/

coverage:
	coverage run --branch -m unittest discover -s tests/
	coverage html

codestyle:
	pycodestyle . --exclude=".#*" --max-line-length=120

.PHONY: init test coverage codestyle
