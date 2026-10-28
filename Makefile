PYTHON ?= .venv/bin/python

.PHONY: fixture-space wheels restic build lint typecheck unit integration maddy-integration runtime-integration acme-integration linux-check browser check plan
fixture-space:
	$(PYTHON) tools/check_fixture_disk.py
restic:
	$(PYTHON) tools/fetch_restic.py linux_amd64
wheels:
	$(PYTHON) -m pip download --require-hashes --only-binary=:all: --platform manylinux_2_34_x86_64 --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform linux_x86_64 --python-version 3.11 --implementation cp --abi cp311 -r requirements-dev.lock --dest build/wheels
build: fixture-space wheels
	docker build --platform linux/amd64 --target runtime -t mail-admin:candidate .
lint:
	$(PYTHON) -m ruff check backend helper host shared tests tools
	cd frontend && npm run lint
typecheck:
	$(PYTHON) -m mypy
	cd frontend && npm run typecheck
unit:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_tests.py unit
integration:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_tests.py integration
maddy-integration: fixture-space wheels restic
	docker build --platform linux/amd64 --target qualification -t mail-admin:qualification .
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_linux.py
runtime-integration: build restic
	docker build --platform linux/amd64 --target qualification -t mail-admin:qualification .
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_linux.py --suite runtime
acme-integration: fixture-space wheels restic
	docker build --platform linux/amd64 --target qualification -t mail-admin:qualification .
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_linux.py --suite acme
linux-check: maddy-integration runtime-integration acme-integration
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_linux.py --suite unit
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/run_linux.py --suite integration
browser: build
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/export_frontend.py
	node tools/check_browser.mjs
reads:
	cd frontend && npm run test:reads
check: lint typecheck reads unit integration
plan:
	PYTHONDONTWRITEBYTECODE=1 python3 docs/mail-admin-implementation/tools/verify_plan.py --run-tests --check-examples
