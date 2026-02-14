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
	cd mothergoose; uv run pytest -v

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

.PHONY: mg-tox-base-3.13
mg-tox-base-3.13:
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

.PHONY: uf-format-check
uf-format-check:
	cd uglyfox; uv run tox -e format

.PHONY: uf-format
uf-format:
	cd uglyfox; uv run isort ./src; uv run black ./src

.PHONY: uf-lint
uf-lint:
	cd uglyfox; uv run tox -e style

.PHONY: uf-type
uf-type:
	cd uglyfox; uv run tox -e type

.PHONY: uf-test
uf-test:
	cd uglyfox; uv run pytest -v

.PHONY: uf-tox-base-3.10
uf-tox-base-3.10:
	cd uglyfox; uv run tox -e 3.10

.PHONY: uf-tox-base-3.11
uf-tox-base-3.11:
	cd uglyfox; uv run tox -e 3.11

.PHONY: uf-tox-base-3.12
uf-tox-base-3.12:
	cd uglyfox; uv run tox -e 3.12

.PHONY: uf-tox-base
uf-tox-base: uf-tox-base-3.10 uf-tox-base-3.11 uf-tox-base-3.12
	cd uglyfox; uv run tox -e 3.13

.PHONY: uf-tox-base-3.13
uf-tox-base-3.13:
	cd uglyfox; uv run tox -e 3.13

.PHONY: uf-tox-all
uf-tox-all:
	cd uglyfox; uv run tox

.PHONY: uf-bump-version-patch
uf-bump-version-patch:
	cd uglyfox; uv version --bump patch;

.PHONY: uf-bump-version-minor
uf-bump-version-minor:
	cd uglyfox; uv version --bump minor;

.PHONY: uf-bump-version-major
uf-bump-version-major:
	cd uglyfox; uv version --bump major;

