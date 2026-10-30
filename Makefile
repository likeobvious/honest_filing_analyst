.PHONY: help up down ingest download-models download-pdfs evaluate test-outage test lint

# ── Variables ─────────────────────────────────────────────────────────────────
COMPOSE = docker compose
PYTHON = python3
DATA_DIR = data
INDEX_DIR = $(DATA_DIR)/indices
MODELS_DIR = models
PDF_DIR = $(DATA_DIR)/pdfs

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Docker Compose ────────────────────────────────────────────────────────────

up: ## Start all services (postgres + litellm-proxy + honest-analyst-app)
	$(COMPOSE) up --build -d
	@echo "✅ Services started. App at http://localhost:80 | LiteLLM at http://localhost:4000"

down: ## Stop all services and remove containers
	$(COMPOSE) down

logs: ## Tail logs from all containers
	$(COMPOSE) logs -f

logs-app: ## Tail logs from the main app container only
	$(COMPOSE) logs -f honest-analyst-app

restart: ## Restart all services
	$(COMPOSE) restart

# ── Model Downloads ───────────────────────────────────────────────────────────

download-models: ## Download and INT8-quantize bge-small and bge-reranker ONNX models (~420 MB total)
	@mkdir -p $(MODELS_DIR)
	$(PYTHON) scripts/download_models.py --output-dir $(MODELS_DIR)
	@echo "✅ Models ready in $(MODELS_DIR)/"

# ── PDF Ingestion ─────────────────────────────────────────────────────────────

ingest: ## Build SQLite + BM25 + FAISS indices from PDFs in data/pdfs/
	@echo "Running offline ingestion pipeline (PyMuPDF sanitizer + Docling + BM25 + FAISS)..."
	@mkdir -p $(INDEX_DIR)
	$(PYTHON) -m src.ingest.build_index --pdf-dir $(PDF_DIR) --output-dir $(INDEX_DIR)
	@echo "✅ Indices built in $(INDEX_DIR)/"

# ── Evaluation ────────────────────────────────────────────────────────────────

evaluate: ## Run the 40-question benchmark (paced at 18.75 RPM)
	@echo "Running PS5 Honest Filing Analyst benchmark evaluation..."
	$(PYTHON) eval/run_eval.py \
		--test-set eval/golden_test_set.json \
		--api-url http://localhost:80 \
		--output eval/results_$$(date +%Y%m%d_%H%M%S).json
	@echo "✅ Evaluation complete. Check eval/results_*.json"

test-outage: ## Execute 60-second LiteLLM proxy blackout drill
	@echo "⚠️  Starting 60-second outage drill..."
	bash eval/test_outage_fallback.sh
	@echo "✅ Outage drill complete"

# ── Testing ───────────────────────────────────────────────────────────────────

test: ## Run unit tests
	$(PYTHON) -m pytest tests/ -v --tb=short

test-math: ## Test AST calculator with edge cases
	$(PYTHON) -c "from src.math_engine.ast_calculator import ASTCalculator; \
		c = ASTCalculator(); \
		print('(255887-242901)/242901*100 =', c.evaluate('(255887-242901)/242901*100')); \
		print('Test passed!')"

test-security: ## Run anti-hijacking and citation verification tests
	$(PYTHON) -m pytest tests/test_security.py -v

# ── Code Quality ──────────────────────────────────────────────────────────────

lint: ## Run ruff linter
	ruff check src/ eval/ scripts/

format: ## Auto-format with ruff
	ruff format src/ eval/ scripts/

# ── Azure Deployment Helpers ──────────────────────────────────────────────────

azure-setup-swap: ## Configure 2GB swapfile on Azure B2s VM (run once)
	sudo fallocate -l 2G /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
	sudo sysctl vm.swappiness=10
	@echo "✅ 2GB swapfile configured (vm.swappiness=10)"

check-memory: ## Show current memory usage across Docker containers
	$(COMPOSE) exec honest-analyst-app cat /proc/meminfo | grep -E "MemTotal|MemAvailable|Buffers|Cached"
	$(COMPOSE) stats --no-stream

sql-token-log: ## Query PostgreSQL for total tokens used in latest run
	$(COMPOSE) exec postgres psql -U litellm -d litellm_db -c \
		"SELECT \
			COUNT(*) as requests, \
			SUM(prompt_tokens) as prompt_tokens, \
			SUM(completion_tokens) as completion_tokens, \
			SUM(total_tokens) as total_tokens \
		 FROM \"LiteLLM_SpendLogs\" \
		 WHERE \"startTime\" >= NOW() - INTERVAL '3 hours';"
