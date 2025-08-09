
.PHONY: mg-format mg-lint mg-type mg-test mg-tox-base mg-tox-all

mg-format:
	cd mothergoose; uv run tox -e format

mg-lint:
	cd mothergoose; uv run tox -e style

mg-type:
	cd mothergoose; uv run tox -e type

mg-test:
	cd mothergoose; uv run pytest

mg-tox-base:
	cd mothergoose; uv run tox -e base

mg-tox-all:
	cd mothergoose; uv run tox
