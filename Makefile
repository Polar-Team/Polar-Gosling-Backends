
.PHONY: format lint type test

format:
	cd mothergoose; uv run black ./src;   uv run isort --profile black ./src

lint:
	cd mothergoose; uv run flake8 ./src;  uv run pylint ./src

type:
	cd mothergoose; uv run mypy ./src

test:
	cd mothergoose; uv run pytest
