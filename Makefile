
.PHONY: mg-format-check mg-format mg-lint mg-type mg-test mg-tox-base-3.10 mg-tox-base-3.11 mg-tox-base-3.12 mg-tox-base mg-tox-all

mg-format-check:
	cd mothergoose; uv run tox -e format

mg-format:
	cd mothergoose; uv run isort ./src; uv run black ./src

mg-lint:
	cd mothergoose; uv run tox -e style

mg-type:
	cd mothergoose; uv run tox -e type

mg-test:
	cd mothergoose; uv run pytest

mg-tox-base-3.10:
	cd mothergoose; uv run tox -e 3.10

mg-tox-base-3.11:
	cd mothergoose; uv run tox -e 3.11

mg-tox-base-3.12:
	cd mothergoose; uv run tox -e 3.12

mg-tox-base: mg-tox-base-3.10 mg-tox-base-3.11 mg-tox-base-3.12
	cd mothergoose; uv run tox -e 3.13

mg-tox-all:
	cd mothergoose; uv run tox
