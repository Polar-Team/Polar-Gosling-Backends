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


# ==============================================================================
# Cloud Stack (Docker Compose) Targets
# ==============================================================================

COMPOSE := docker compose -f compose/docker-compose.yml
PROFILES_UP := --profile seed

# _preflight — guard that docker and docker compose are available on PATH.
# Exits 1 with an error naming the missing dependency. Checks complete well
# within the 5-second budget required by Requirement 10.9.
define _preflight
	@command -v docker >/dev/null 2>&1 || \
		{ echo "ERROR: 'docker' not found on PATH"; exit 1; }
	@docker compose version >/dev/null 2>&1 || \
		{ echo "ERROR: 'docker compose' plugin not found"; exit 1; }
endef

.PHONY: compose-up compose-down compose-reset compose-logs compose-ps compose-smoke compose-clean compose-seed compose-config compose-check

# ------------------------------------------------------------------------------
# compose-up: Start Cloud_Stack in detached mode, wait for healthy (180s max).
# On failure, reports mothergoose-api's last observed health status.
# Requirements: 10.1, 10.2, 14.1, 14.2
# ------------------------------------------------------------------------------
compose-up:
	$(_preflight)
	@echo "Starting Cloud Stack (detached, waiting up to 180s for healthy)..."
	$(COMPOSE) $(PROFILES_UP) up -d --wait --wait-timeout 180 || \
		{ \
			echo "ERROR: compose-up failed. mothergoose-api last health: $$(docker inspect --format='{{.State.Health.Status}}' pg-stack-mothergoose-api 2>/dev/null || echo 'unknown')"; \
			exit 1; \
		}

# ------------------------------------------------------------------------------
# compose-down: Stop and remove containers, preserve volumes.
# Requirements: 10.3, 14.3
# ------------------------------------------------------------------------------
compose-down:
	$(_preflight)
	@echo "Stopping Cloud Stack (preserving volumes)..."
	$(COMPOSE) --profile seed --profile with-triggers down

# ------------------------------------------------------------------------------
# compose-up-all: Start Cloud_Stack with all profiles (seed + with-triggers).
# Includes the trigger-emulator and full pipeline.
# ------------------------------------------------------------------------------
compose-up-all:
	$(_preflight)
	@echo "Starting Cloud Stack with all profiles (seed + with-triggers)..."
	$(COMPOSE) --profile seed --profile with-triggers up -d --wait --wait-timeout 180 || \
		{ \
			echo "ERROR: compose-up-all failed. mothergoose-api last health: $$(docker inspect --format='{{.State.Health.Status}}' pg-stack-mothergoose-api 2>/dev/null || echo 'unknown')"; \
			exit 1; \
		}

# ------------------------------------------------------------------------------
# compose-reset: Full reset — remove containers + volumes, then start fresh.
# Requirements: 10.4
# ------------------------------------------------------------------------------
compose-reset:
	$(_preflight)
	@echo "Resetting Cloud Stack (removing volumes)..."
	$(COMPOSE) down -v
	$(MAKE) compose-up

# ------------------------------------------------------------------------------
# compose-logs: Stream combined logs until interrupted.
# Requirements: 10.5
# ------------------------------------------------------------------------------
compose-logs:
	$(_preflight)
	$(COMPOSE) logs -f

# ------------------------------------------------------------------------------
# compose-smoke: Run Pipeline_Smoke_Test against a running Cloud Stack.
# Exits non-zero if the stack is not running or if the smoke test fails.
# Requirements: 10.6, 10.7
# ------------------------------------------------------------------------------
compose-smoke:
	$(_preflight)
	@RUNNING_SERVICES=$$($(COMPOSE) ps --services --filter status=running); \
	if [ -z "$$RUNNING_SERVICES" ]; then \
		echo "ERROR: Cloud Stack is not running"; \
		exit 1; \
	fi
	cd compose && \
	uv run python scripts/smoke_test.py

# ------------------------------------------------------------------------------
# compose-smoke-triggers: Extended smoke test with trigger emulator + UglyFox.
# Starts the stack with the `with-triggers` profile, waits for the trigger
# emulator to fire at least one sync, and verifies UglyFox task processing.
# ------------------------------------------------------------------------------
compose-smoke-triggers:
	$(_preflight)
	@RUNNING_SERVICES=$$($(COMPOSE) --profile seed --profile with-triggers ps --services --filter status=running); \
	if [ -z "$$RUNNING_SERVICES" ]; then \
		echo "ERROR: Cloud Stack (with-triggers) is not running. Start with:"; \
		echo "  COMPOSE_PROFILES=seed,with-triggers make compose-up"; \
		exit 1; \
	fi
	cd compose && \
	SMOKE_TEST_TRIGGERS=1 uv run python scripts/smoke_test.py

# ------------------------------------------------------------------------------
# compose-clean: Remove all containers, volumes, and dangling pg-stack images.
# Requirements: 10.8
# ------------------------------------------------------------------------------
compose-clean:
	$(_preflight)
	$(COMPOSE) down -v
	docker image prune --filter "label=pg-stack=true" -f

# ------------------------------------------------------------------------------
# compose-config: Validate the Compose file syntax (quiet mode).
# Requirements: 14.7
# ------------------------------------------------------------------------------
compose-config:
	$(_preflight)
	$(COMPOSE) config -q

# ------------------------------------------------------------------------------
# compose-check: Run static validation scripts and compose config check.
# Requirements: 14.8
# ------------------------------------------------------------------------------
compose-check:
	$(_preflight)
	uv run python compose/scripts/check_compose.py
	uv run python compose/scripts/check_env.py
	$(COMPOSE) config -q
