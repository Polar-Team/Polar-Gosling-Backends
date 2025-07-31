
.PHONY: format lint type test tox-base tox-all

format:
	cd mothergoose; uv run tox -e format

lint:
	cd mothergoose; uv run tox -e style

type:
	cd mothergoose; uv run tox -e type

test:
	cd mothergoose; uv run pytest

tox-base:
	cd mothergoose; uv run tox -e base

tox-all:
	cd mothergoose; uv run tox
