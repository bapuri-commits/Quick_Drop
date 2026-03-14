import os
from pathlib import Path

SYOPS_SECRET_KEY = os.getenv("SYOPS_SECRET_KEY", "dev-secret")
SYOPS_API_URL = os.getenv("SYOPS_API_URL", "http://host.docker.internal:8300")

# Drop (임시 전송)
UPLOAD_DIR = Path(os.getenv("QUICKDROP_UPLOAD_DIR", "./uploads"))
MAX_FILE_BYTES = int(os.getenv("QUICKDROP_MAX_FILE_MB", "500")) * 1024 * 1024
MAX_STORAGE_BYTES = int(os.getenv("QUICKDROP_MAX_STORAGE_GB", "10")) * 1024 ** 3
DEFAULT_EXPIRE_DAYS = int(os.getenv("QUICKDROP_EXPIRE_DAYS", "7"))

# Vault (영구 보관)
VAULT_DIR = Path(os.getenv("QUICKDROP_VAULT_DIR", "./vault"))
MAX_VAULT_BYTES = int(os.getenv("QUICKDROP_MAX_VAULT_GB", "10")) * 1024 ** 3

# Clipboard (클립보드)
CLIPBOARD_DIR = Path(os.getenv("QUICKDROP_CLIPBOARD_DIR", "./clipboard"))
MAX_CLIP_TEXT_BYTES = 1 * 1024 * 1024       # 텍스트 1MB
MAX_CLIP_IMAGE_BYTES = 10 * 1024 * 1024     # 이미지 10MB
MAX_CLIPBOARD_BYTES = int(os.getenv("QUICKDROP_MAX_CLIPBOARD_GB", "1")) * 1024 ** 3

# Google Drive
GDRIVE_SERVICE_ACCOUNT = os.getenv("GDRIVE_SERVICE_ACCOUNT_PATH", "")
GDRIVE_ROOT_FOLDER_ID = os.getenv("GDRIVE_ROOT_FOLDER_ID", "")
GDRIVE_ENABLED = bool(GDRIVE_SERVICE_ACCOUNT and GDRIVE_ROOT_FOLDER_ID)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
VAULT_DIR.mkdir(parents=True, exist_ok=True)
CLIPBOARD_DIR.mkdir(parents=True, exist_ok=True)
