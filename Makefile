# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test:
	pytest tests/ -v

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
lint:
	ruff check .

format:
	black .

type: 
	mypy src/archivyst/

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean: ## Remove Python and tool caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .mypy_cache 2>/dev/null || true
	rm -rf .ruff_cache 2>/dev/null || true
	rm -rf .pytest_cache 2>/dev/null || true

clean-logs: ## Delete log files
	rm -f ~/.config/archivyst/archivyst.log*

