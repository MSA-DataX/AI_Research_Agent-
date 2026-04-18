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
