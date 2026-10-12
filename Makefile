# Local dev shortcuts. Run on WSL/Git Bash/macOS; Windows CMD needs GNU Make.

.PHONY: help install test lint fmt package clean dynamodb-local dynamodb-down

help:
	@echo "Targets:"
	@echo "  install         Install dev dependencies into ./venv"
	@echo "  test            Run pytest (requires dynamodb-local running)"
	@echo "  lint            Run ruff check"
	@echo "  fmt             Run ruff format"
	@echo "  package         Build all 3 Lambda deployment zips under dist/"
	@echo "  dynamodb-local  Start DynamoDB Local in Docker (port 8000)"
	@echo "  dynamodb-down   Stop DynamoDB Local"
	@echo "  clean           Remove build artifacts"

install:
	python -m venv venv
	./venv/bin/pip install -r requirements-dev.txt

lint:
	./venv/bin/ruff check shared services scripts tests

fmt:
	./venv/bin/ruff format shared services scripts tests

test:
	./venv/bin/pytest -v

dynamodb-local:
	docker run -d --rm --name skatebot-ddb -p 8000:8000 amazon/dynamodb-local

dynamodb-down:
	docker stop skatebot-ddb || true

package:
	python scripts/package_lambdas.py

clean:
	rm -rf dist services/*/build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
