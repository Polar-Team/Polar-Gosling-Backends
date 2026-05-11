SHELL := bash

# ---------------------------------------------------------------------------
# Version extraction (reads from each service's pyproject.toml)
# ---------------------------------------------------------------------------
MG_VERSION := $(shell grep -m1 '^version' mothergoose/pyproject.toml | sed 's/.*"\(.*\)"/\1/')
UF_VERSION := $(shell grep -m1 '^version' uglyfox/pyproject.toml | sed 's/.*"\(.*\)"/\1/')

# ---------------------------------------------------------------------------
# Container registry
# ---------------------------------------------------------------------------
REGISTRY     ?= ghcr.io/polar-team
MG_IMAGE     := $(REGISTRY)/mothergoose
UF_IMAGE     := $(REGISTRY)/uglyfox
PLATFORMS    ?= linux/amd64,linux/arm64

# ---------------------------------------------------------------------------
# Docker — MotherGoose
# ---------------------------------------------------------------------------

.PHONY: mg-docker-local
mg-docker-local: ## Build MotherGoose image locally (plain docker build, no buildx)
	docker build \
		-f Dockerfile.mtg \
		-t $(MG_IMAGE):$(MG_VERSION) \
		-t $(MG_IMAGE):latest \
		.

.PHONY: mg-docker-build
mg-docker-build: ## Build MotherGoose image for the local platform (buildx)
	docker buildx build \
		-f Dockerfile.mtg \
		-t $(MG_IMAGE):$(MG_VERSION) \
		-t $(MG_IMAGE):latest \
		--load \
		.

.PHONY: mg-docker-push
mg-docker-push: ## Build & push MotherGoose multi-arch image
	docker buildx build \
		-f Dockerfile.mtg \
		--platform $(PLATFORMS) \
		-t $(MG_IMAGE):$(MG_VERSION) \
		-t $(MG_IMAGE):latest \
		--push \
		.

# ---------------------------------------------------------------------------
# Docker — UglyFox
# ---------------------------------------------------------------------------

.PHONY: uf-docker-local
uf-docker-local: ## Build UglyFox image locally (plain docker build, no buildx)
	docker build \
		-f Dockerfile.uf \
		-t $(UF_IMAGE):$(UF_VERSION) \
		-t $(UF_IMAGE):latest \
		.

.PHONY: uf-docker-build
uf-docker-build: ## Build UglyFox image for the local platform (buildx)
	docker buildx build \
		-f Dockerfile.uf \
		-t $(UF_IMAGE):$(UF_VERSION) \
		-t $(UF_IMAGE):latest \
		--load \
		.

.PHONY: uf-docker-push
uf-docker-push: ## Build & push UglyFox multi-arch image
	docker buildx build \
		-f Dockerfile.uf \
		--platform $(PLATFORMS) \
		-t $(UF_IMAGE):$(UF_VERSION) \
		-t $(UF_IMAGE):latest \
		--push \
		.

# ---------------------------------------------------------------------------
# Docker — Both services
# ---------------------------------------------------------------------------

.PHONY: docker-local-all
docker-local-all: mg-docker-local uf-docker-local ## Build both images locally (plain docker build)

.PHONY: docker-build-all
docker-build-all: mg-docker-build uf-docker-build ## Build both images locally (buildx)

.PHONY: docker-push-all
docker-push-all: mg-docker-push uf-docker-push ## Build & push both multi-arch images

# ---------------------------------------------------------------------------
# Mothergoose

.PHONY: mg-format-check
mg-format-check:
	cd mothergoose && uv run tox -e format

.PHONY: mg-format
mg-format:
	cd mothergoose && uv run isort ./src && uv run black ./src

.PHONY: mg-lint
mg-lint:
	cd mothergoose && uv run tox -e style

.PHONY: mg-type
mg-type:
	cd mothergoose && uv run tox -e type

.PHONY: mg-test
mg-test:
	cd mothergoose && uv run pytest -v

.PHONY: mg-tox-base-3.10
mg-tox-base-3.10:
	cd mothergoose && uv run tox -e 3.10

.PHONY: mg-tox-base-3.11
mg-tox-base-3.11:
	cd mothergoose && uv run tox -e 3.11

.PHONY: mg-tox-base-3.12
mg-tox-base-3.12:
	cd mothergoose && uv run tox -e 3.12

.PHONY: mg-tox-base
mg-tox-base: mg-tox-base-3.10 mg-tox-base-3.11 mg-tox-base-3.12
	cd mothergoose && uv run tox -e 3.13

.PHONY: mg-tox-base-3.13
mg-tox-base-3.13:
	cd mothergoose && uv run tox -e 3.13

.PHONY: mg-tox-all
mg-tox-all:
	cd mothergoose && uv run tox

.PHONY: mg-bump-version-patch
mg-bump-version-patch:
	cd mothergoose && uv version --bump patch

.PHONY: mg-bump-version-minor
mg-bump-version-minor:
	cd mothergoose && uv version --bump minor

.PHONY: mg-bump-version-major
mg-bump-version-major:
	cd mothergoose && uv version --bump major

# UglyFox

.PHONY: uf-format-check
uf-format-check:
	cd uglyfox && uv run tox -e format

.PHONY: uf-format
uf-format:
	cd uglyfox && uv run isort ./src && uv run black ./src

.PHONY: uf-lint
uf-lint:
	cd uglyfox && uv run tox -e style

.PHONY: uf-type
uf-type:
	cd uglyfox && uv run tox -e type

.PHONY: uf-test
uf-test:
	cd uglyfox && uv run pytest -v

.PHONY: uf-tox-base-3.10
uf-tox-base-3.10:
	cd uglyfox && uv run tox -e 3.10

.PHONY: uf-tox-base-3.11
uf-tox-base-3.11:
	cd uglyfox && uv run tox -e 3.11

.PHONY: uf-tox-base-3.12
uf-tox-base-3.12:
	cd uglyfox && uv run tox -e 3.12

.PHONY: uf-tox-base
uf-tox-base: uf-tox-base-3.10 uf-tox-base-3.11 uf-tox-base-3.12
	cd uglyfox && uv run tox -e 3.13

.PHONY: uf-tox-base-3.13
uf-tox-base-3.13:
	cd uglyfox && uv run tox -e 3.13

.PHONY: uf-tox-all
uf-tox-all:
	cd uglyfox && uv run tox

.PHONY: uf-bump-version-patch
uf-bump-version-patch:
	cd uglyfox && uv version --bump patch

.PHONY: uf-bump-version-minor
uf-bump-version-minor:
	cd uglyfox && uv version --bump minor

.PHONY: uf-bump-version-major
uf-bump-version-major:
	cd uglyfox && uv version --bump major

