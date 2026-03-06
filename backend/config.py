import os
from pathlib import Path

PASSWORD = os.getenv("QUICKDROP_PASSWORD", "changeme")
SECRET_KEY = os.getenv("QUICKDROP_SECRET_KEY", "dev-secret-key-change-in-prod")

UPLOAD_DIR = Path(os.getenv("QUICKDROP_UPLOAD_DIR", "./uploads"))
MAX_FILE_BYTES = int(os.getenv("QUICKDROP_MAX_FILE_MB", "500")) * 1024 * 1024
MAX_STORAGE_BYTES = int(os.getenv("QUICKDROP_MAX_STORAGE_GB", "10")) * 1024 ** 3
DEFAULT_EXPIRE_DAYS = int(os.getenv("QUICKDROP_EXPIRE_DAYS", "7"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
