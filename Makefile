
.PHONY: format lint type test tox-3.10 tox-3.11 tox-3.12 tox-3.13 tox-all

format:
	cd mothergoose; uv run black ./src;   uv run isort --profile black ./src

lint:
	cd mothergoose; uv run flake8 ./src;  uv run pylint ./src

type:
	cd mothergoose; uv run mypy ./src

test:
	cd mothergoose; uv run pytest

tox-3.10:
	cd mothergoose; uv run tox -e 3.10

tox-3.11:
	cd mothergoose; uv run tox -e 3.11

tox-3.12:
	cd mothergoose; uv run tox -e 3.12

tox-3.13:
	cd mothergoose; uv run tox -e 3.13

tox-all:
	cd mothergoose; uv run tox
