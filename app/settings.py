"""运行期常量与路径。测试可通过环境变量覆盖 DB_PATH。"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "crawlscope"
APP_TITLE = "crawlscope · 政务公开目录采集与 sitemap 收录对账"

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
SAMPLE_DATA_DIR = PROJECT_DIR / "sample_data"
SITE_PROFILES_FILE = SAMPLE_DATA_DIR / "site_profiles.json"

DEFAULT_SAMPLE_SITE_URL = os.environ.get("SAMPLE_SITE_URL", "http://sample-site")
DEFAULT_MAX_PAGES = 10
MAX_PAGES_LIMIT = 30
DEFAULT_POLICY = "tolerant"
FETCH_TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "5"))
FETCH_RETRIES = 1
SEED_VERSION = "1"

POLICIES = {
    "tolerant": "完整核对（记录重复页后继续，默认）",
    "strict": "严格：遇重复页立即停止",
}


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/app/data"))


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH", str(data_dir() / "crawlscope.db")))
