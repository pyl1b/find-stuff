PYTHON_FILES := $(wildcard *.py)


init:
	python -m pip install -e .


init-d:
	python -m pip install --upgrade pip
	python -m pip install -e .[dev]


test:
	mypy find_stuff tests
	pytest


lint:
	ruff check --force-exclude .


format:
	ruff format --force-exclude .


delint: format
	ruff check --fix --force-exclude .
