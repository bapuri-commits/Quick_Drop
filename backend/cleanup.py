"""Expired file cleanup — run via cron: python cleanup.py"""

import json
import time

import config

META_DIR = config.UPLOAD_DIR / ".meta"
CLIP_META_DIR = config.CLIPBOARD_DIR / ".meta"


def cleanup_drop():
    removed = 0
    for meta_file in list(META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if time.time() > meta["expire_at"]:
            file_path = config.UPLOAD_DIR / meta["stored_name"]
            file_path.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            removed += 1
            print(f"  [drop] removed: {meta['original_name']} (expired)")
    return removed


def cleanup_clipboard():
    removed = 0
    if not CLIP_META_DIR.exists():
        return removed
    for meta_file in list(CLIP_META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        expire_at = meta.get("expire_at")
        if expire_at is None:
            continue
        if time.time() > expire_at:
            if meta.get("image_name"):
                img_path = config.CLIPBOARD_DIR / meta["image_name"]
                img_path.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            removed += 1
            print(f"  [clip] removed: {meta['title']} (expired)")
    return removed


def cleanup():
    drop_removed = cleanup_drop()
    clip_removed = cleanup_clipboard()
    print(f"cleanup done: {drop_removed} files, {clip_removed} clips removed")


if __name__ == "__main__":
    cleanup()
