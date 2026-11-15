.PHONY: install run dev test lint format check clean \
	docker-build docker-up docker-down docker-logs docker-seed docker-config docker-shell

DOCKER_COMPOSE ?= docker compose

install:  ## Install runtime and development dependencies
	uv sync --all-groups

run:  ## Start Getflix
	uv run getflix

dev:  ## Start Getflix with the Flask reloader
	GETFLIX_DEBUG=true uv run getflix

test:  ## Run the test suite
	uv run pytest -q

lint:  ## Check code style
	uv run ruff check .

format:  ## Apply automatic fixes
	uv run ruff check --fix .
	uv run ruff format .

check: lint test  ## Lint and test

clean:  ## Remove caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache

docker-build:  ## Build the Getflix container image
	$(DOCKER_COMPOSE) build

docker-up:  ## Build and start Getflix plus the VPN protected qBittorrent
	$(DOCKER_COMPOSE) up -d --build

docker-down:  ## Stop the stack and remove the containers
	$(DOCKER_COMPOSE) down

docker-logs:  ## Follow the logs of every service
	$(DOCKER_COMPOSE) logs -f

docker-seed:  ## Re-apply the qBittorrent password and install the search plugins
	$(DOCKER_COMPOSE) run --rm qbittorrent-init

docker-config:  ## Show the resolved compose configuration
	$(DOCKER_COMPOSE) config

docker-shell:  ## Open a shell in the running Getflix container
	$(DOCKER_COMPOSE) exec getflix /bin/bash
