# Makefile for toolregistry-server package

# Variables
PACKAGE_NAME := toolregistry-server
DIST_DIR := dist
VERSION := $(shell grep -oE '__version__[[:space:]]*=[[:space:]]*"[^"]+"' src/toolregistry_server/__init__.py | grep -oE '"[^"]+"' | tr -d '"' || echo "0.1.0")

# Default target
all: lint test build

# ──────────────────────────────────────────────
# Linting & Type Checking
# ──────────────────────────────────────────────

# Run all linting and type checking
lint:
	@echo "Running ruff check..."
	ruff check src/ tests/
	@echo "Running ruff format check..."
	ruff format --check src/ tests/
	@echo "Running ty check..."
	ty check src/
	@echo "Running complexipy..."
	complexipy . -e src/toolregistry_server/_vendor -mx 20
	@echo "All checks passed."

# Auto-fix and format
fmt:
	@echo "Running ruff fix..."
	ruff check --fix src/ tests/
	@echo "Running ruff format..."
	ruff format src/ tests/
	@echo "Format complete."

# ──────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────

# Run tests
test:
	@echo "Running tests..."
	pytest tests/ -v --tb=short
	@echo "Tests completed."

# ──────────────────────────────────────────────
# Package targets
# ──────────────────────────────────────────────

# Build the Python package
build: clean
	@echo "Building $(PACKAGE_NAME) package..."
	python -m build
	@echo "Build complete. Distribution files are in $(DIST_DIR)/"

# Push the package to PyPI
push:
	@echo "Pushing $(PACKAGE_NAME) to PyPI..."
	twine upload $(DIST_DIR)/*
	@echo "Package pushed to PyPI."

# Clean up build and distribution files
clean:
	@echo "Cleaning up build and distribution files..."
	rm -rf $(DIST_DIR) *.egg-info build/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	@echo "Cleanup complete."


# Help target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Development:"
	@echo "  lint           - Run ruff, ty, and complexipy checks"
	@echo "  fmt            - Auto-fix and format with ruff"
	@echo "  test           - Run tests with pytest"
	@echo ""
	@echo "Package targets:"
	@echo "  build  - Build the Python package"
	@echo "  push   - Push the package to PyPI"
	@echo "  clean  - Clean up build and distribution files"
	@echo ""
	@echo "Composite targets:"
	@echo "  all            - Run lint, test, and build (default)"
	@echo ""
	@echo "Detected version: $(VERSION)"

.PHONY: all lint fmt test build push clean help
