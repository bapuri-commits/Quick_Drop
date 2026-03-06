"""Expired file cleanup — run via cron: python cleanup.py"""

import json
import time

import config

META_DIR = config.UPLOAD_DIR / ".meta"


def cleanup():
    removed = 0
    for meta_file in list(META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if time.time() > meta["expire_at"]:
            file_path = config.UPLOAD_DIR / meta["stored_name"]
            file_path.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            removed += 1
            print(f"  removed: {meta['original_name']} (expired)")
    print(f"cleanup done: {removed} files removed")


if __name__ == "__main__":
    cleanup()
