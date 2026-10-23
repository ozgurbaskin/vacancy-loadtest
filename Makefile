# Developer entry points; `make help` lists them. Commands run through uv by default; after
# `pip install -r requirements-dev.txt -e .` in your own virtualenv use `make RUN= <target>`.
RUN   ?= uv run
HOST  ?= vacancies.cyrextech.net:7823
USERS ?= 3
RATE  ?= 1
TIME  ?= 5m
OUT   ?= reports/latest

.PHONY: help install proto proto-check lint format test smoke fake-server run headless dist clean

help: ## list the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## create .venv and install runtime + dev dependencies (uv)
	uv sync

proto: ## regenerate src/vacancy_loadtest/pb from proto/
	$(RUN) python scripts/gen_proto.py

proto-check: ## fail if the committed stubs are out of date
	$(RUN) python scripts/gen_proto.py --check

lint: ## ruff: lint + formatting check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format: ## ruff: format and auto-fix
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

test: ## pytest against the in-memory fake server (no network needed)
	$(RUN) pytest

smoke: ## 20-second headless Locust run against the fake server (what CI runs)
	RUN="$(RUN)" ./scripts/smoke.sh

fake-server: ## run the fake server on :50051 with the example accounts, for local demos
	$(RUN) python -m vacancy_loadtest.fake_server --port 50051 \
	    --credentials credentials.example.json --seed-vacancies 25 --latency-ms 10

run: ## Locust web UI (http://localhost:8089) against $(HOST)
	$(RUN) locust -f locustfile.py --host $(HOST)

headless: ## headless run, CSV + HTML report into $(OUT): make headless USERS=3 RATE=1 TIME=5m
	mkdir -p $(OUT)
	$(RUN) locust -f locustfile.py --host $(HOST) --headless \
	    -u $(USERS) -r $(RATE) -t $(TIME) --csv $(OUT)/run --html $(OUT)/run.html

dist: ## zip of the tracked files for submission (credentials.json is never included)
	git archive --format=zip --prefix=vacancy-loadtest/ -o vacancy-loadtest.zip HEAD
	@echo "wrote vacancy-loadtest.zip"

clean: ## remove the virtualenv, caches, local reports and build artefacts
	rm -rf .venv .pytest_cache .ruff_cache reports/latest vacancy-loadtest.zip
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
