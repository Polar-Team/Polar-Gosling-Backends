.PHONY: mg-format-check
mg-format-check:
	cd mothergoose; uv run tox -e format

.PHONY: mg-format
mg-format:
	cd mothergoose; uv run isort ./src; uv run black ./src

.PHONY: mg-lint
mg-lint:
	cd mothergoose; uv run tox -e style

.PHONY: mg-type
mg-type:
	cd mothergoose; uv run tox -e type

.PHONY: mg-test
mg-test:
	cd mothergoose; uv run pytest

.PHONY: mg-tox-base-3.10
mg-tox-base-3.10:
	cd mothergoose; uv run tox -e 3.10

.PHONY: mg-tox-base-3.11
mg-tox-base-3.11:
	cd mothergoose; uv run tox -e 3.11

.PHONY: mg-tox-base-3.12
mg-tox-base-3.12:
	cd mothergoose; uv run tox -e 3.12

.PHONY: mg-tox-base
mg-tox-base: mg-tox-base-3.10 mg-tox-base-3.11 mg-tox-base-3.12
	cd mothergoose; uv run tox -e 3.13

.PHONY: mg-tox-all
mg-tox-all:
	cd mothergoose; uv run tox

.PHONY: mg-bump-version-patch
mg-bump-version-patch:
	cd mothergoose; uv version --bump patch;

.PHONY: mg-bump-version-minor
mg-bump-version-minor:
	cd mothergoose; uv version --bump minor;

.PHONY: mg-bump-version-major
mg-bump-version-major:
	cd mothergoose; uv version --bump major;
