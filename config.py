import os

LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
LM_STUDIO_API_KEY = os.getenv("LM_STUDIO_API_KEY", "lm-studio")
MODEL_NAME = os.getenv("MODEL_NAME", "local-model")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5")

MAX_ITERATIONS = 15
REQUEST_TIMEOUT = 120
ENABLE_PLANNING = True
ENABLE_EMBEDDING_MEMORY = True

_BASE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(_BASE, "results")
LOG_DIR = os.path.join(_BASE, "logs")
MEMORY_PATH = os.path.join(_BASE, "memory.json")
DB_PATH = os.path.join(_BASE, "research.db")

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

SIMILARITY_REUSE_THRESHOLD = 0.72

AUTO_CLEANUP_ENABLED = os.getenv("AUTO_CLEANUP", "1") != "0"
AUTO_CLEANUP_INTERVAL_HOURS = int(os.getenv("AUTO_CLEANUP_INTERVAL_HOURS", "24"))
AUTO_CLEANUP_LOGS_DAYS = int(os.getenv("AUTO_CLEANUP_LOGS_DAYS", "30"))
AUTO_CLEANUP_RESULTS_DAYS = int(os.getenv("AUTO_CLEANUP_RESULTS_DAYS", "30"))
AUTO_CLEANUP_RBS_DAYS = int(os.getenv("AUTO_CLEANUP_RBS_DAYS", "90"))
AUTO_CLEANUP_MARKER = os.path.join(_BASE, ".last_cleanup")

API_KEY = os.getenv("API_KEY", "").strip()
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "0"))
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501").split(",")
    if o.strip()
]
