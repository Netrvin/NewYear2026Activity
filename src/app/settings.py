"""Application settings and environment configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)

# Telegram settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# OpenAI settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Admin settings
ADMIN_USER_IDS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("ADMIN_USER_IDS", "").split(",")
    if uid.strip()
]

# Database settings
DATABASE_BACKEND = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "activity.db")))

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "new_year_2026_activity")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_CHARSET = os.getenv("MYSQL_CHARSET", "utf8mb4")
MYSQL_CONNECT_TIMEOUT = float(os.getenv("MYSQL_CONNECT_TIMEOUT", "5"))
MYSQL_POOL_MIN_SIZE = int(os.getenv("MYSQL_POOL_MIN_SIZE", "1"))
MYSQL_POOL_MAX_SIZE = int(os.getenv("MYSQL_POOL_MAX_SIZE", "10"))

# Logging settings
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Config file paths
ACTIVITY_CONFIG_PATH = CONFIG_DIR / "activity.json"
LEVELS_CONFIG_PATH = CONFIG_DIR / "levels.json"
REWARDS_CONFIG_PATH = CONFIG_DIR / "rewards.json"
