"""QuickDrop — Personal file cloud API."""

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from fastapi import (
    Cookie,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config

ALLOWED_ORIGIN = os.getenv(
    "QUICKDROP_ALLOWED_ORIGIN", "https://drop.syworkspace.cloud"
)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="QuickDrop", docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return Response(
        content='{"detail":"Too many requests"}',
        status_code=429,
        media_type="application/json",
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

META_DIR = config.UPLOAD_DIR / ".meta"
META_DIR.mkdir(exist_ok=True)

CLIP_META_DIR = config.CLIPBOARD_DIR / ".meta"
CLIP_META_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

TOKEN_LIFETIME = 60 * 60 * 24 * 30  # 30 days
_valid_tokens: dict[str, float] = {}


_FILE_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _require_auth(session: str | None, token: str | None = None):
    effective = session or token
    if not effective or effective not in _valid_tokens:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if time.time() - _valid_tokens[effective] > TOKEN_LIFETIME:
        _valid_tokens.pop(effective, None)
        raise HTTPException(status_code=401, detail="Session expired")


def _validate_file_id(file_id: str):
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID")


def _safe_filename(name: str | None) -> str:
    """Extract basename and strip path traversal components."""
    if not name:
        return "unnamed"
    safe = Path(name).name
    if not safe or safe in (".", ".."):
        return "unnamed"
    return safe


def _read_meta(file_id: str) -> dict | None:
    meta_path = META_DIR / f"{file_id}.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_meta(file_id: str, meta: dict):
    meta_path = META_DIR / f"{file_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _delete_file(file_id: str):
    meta = _read_meta(file_id)
    if meta:
        file_path = config.UPLOAD_DIR / meta["stored_name"]
        file_path.unlink(missing_ok=True)
        (META_DIR / f"{file_id}.json").unlink(missing_ok=True)


def _total_storage_used() -> int:
    total = 0
    for meta_file in META_DIR.glob("*.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        total += meta.get("size", 0)
    return total


# ── Auth ──────────────────────────────────────────────

@app.post("/api/auth")
@limiter.limit("5/minute")
async def auth(request: Request, response: Response, password: str = Form(...)):
    if not secrets.compare_digest(password, config.PASSWORD):
        raise HTTPException(status_code=403, detail="Wrong password")
    token = uuid.uuid4().hex
    _valid_tokens[token] = time.time()
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=TOKEN_LIFETIME,
    )
    return {"ok": True, "token": token}


@app.get("/api/auth/check")
async def auth_check(session: str | None = Cookie(default=None)):
    if session and session in _valid_tokens:
        if time.time() - _valid_tokens[session] <= TOKEN_LIFETIME:
            return {"authenticated": True}
    raise HTTPException(status_code=401, detail="Unauthorized")


# ── Files ─────────────────────────────────────────────

@app.get("/api/files")
async def list_files(session: str | None = Cookie(default=None)):
    _require_auth(session)
    files = []
    for meta_file in sorted(META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        remaining = meta["expire_at"] - time.time()
        if remaining <= 0:
            _delete_file(meta["id"])
            continue
        files.append({
            "id": meta["id"],
            "name": meta["original_name"],
            "size": meta["size"],
            "uploaded_at": meta["uploaded_at"],
            "expire_at": meta["expire_at"],
            "remaining_hours": round(remaining / 3600, 1),
        })
    files.sort(key=lambda f: f["uploaded_at"], reverse=True)
    return {"files": files, "storage_used": _total_storage_used()}


@app.post("/api/files")
async def upload_files(
    files: list[UploadFile] = File(...),
    expire_days: int = Form(default=config.DEFAULT_EXPIRE_DAYS),
    session: str | None = Cookie(default=None),
):
    _require_auth(session)
    expire_days = max(1, min(expire_days, 30))
    results = []

    for f in files:
        content = await f.read()
        if len(content) > config.MAX_FILE_BYTES:
            results.append({"name": f.filename, "error": "File too large"})
            continue

        if _total_storage_used() + len(content) > config.MAX_STORAGE_BYTES:
            results.append({"name": f.filename, "error": "Storage full"})
            continue

        file_id = uuid.uuid4().hex[:12]
        ext = Path(f.filename or "file").suffix
        stored_name = f"{file_id}{ext}"
        file_path = config.UPLOAD_DIR / stored_name
        file_path.write_bytes(content)

        meta = {
            "id": file_id,
            "original_name": f.filename,
            "stored_name": stored_name,
            "size": len(content),
            "uploaded_at": time.time(),
            "expire_at": time.time() + expire_days * 86400,
        }
        _write_meta(file_id, meta)
        results.append({"name": f.filename, "id": file_id, "size": len(content)})

    return {"uploaded": results}


@app.get("/api/files/{file_id}/download")
async def download_file(file_id: str, token: str | None = None, session: str | None = Cookie(default=None)):
    _validate_file_id(file_id)
    _require_auth(session, token)
    meta = _read_meta(file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    if time.time() > meta["expire_at"]:
        _delete_file(file_id)
        raise HTTPException(status_code=404, detail="File expired")
    file_path = config.UPLOAD_DIR / meta["stored_name"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(
        path=str(file_path),
        filename=meta["original_name"],
        media_type="application/octet-stream",
    )


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str, session: str | None = Cookie(default=None)):
    _validate_file_id(file_id)
    _require_auth(session)
    meta = _read_meta(file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    _delete_file(file_id)
    return {"ok": True}


@app.delete("/api/files")
async def delete_all_files(session: str | None = Cookie(default=None)):
    _require_auth(session)
    count = 0
    for meta_file in list(META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        _delete_file(meta["id"])
        count += 1
    return {"deleted": count}


# ── Vault (permanent storage) ─────────────────────────

def _safe_vault_path(path_str: str) -> Path:
    """Resolve vault path and prevent directory traversal."""
    cleaned = path_str.strip("/").replace("\\", "/")
    resolved = (config.VAULT_DIR / cleaned).resolve()
    if not str(resolved).startswith(str(config.VAULT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return resolved


def _vault_storage_used() -> int:
    total = 0
    for f in config.VAULT_DIR.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


@app.get("/api/vault")
async def vault_list(path: str = "/", session: str | None = Cookie(default=None)):
    _require_auth(session)
    target = _safe_vault_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    items = []
    for item in sorted(target.iterdir()):
        if item.name.startswith("."):
            continue
        entry = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
        if item.is_file():
            stat = item.stat()
            entry["size"] = stat.st_size
            entry["modified_at"] = stat.st_mtime
        items.append(entry)

    rel = "/" + str(target.relative_to(config.VAULT_DIR.resolve())).replace("\\", "/")
    if rel == "/.":
        rel = "/"
    return {"path": rel, "items": items, "storage_used": _vault_storage_used()}


@app.post("/api/vault")
async def vault_upload(
    files: list[UploadFile] = File(...),
    path: str = Form(default="/"),
    session: str | None = Cookie(default=None),
):
    _require_auth(session)
    target_dir = _safe_vault_path(path)
    target_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for f in files:
        content = await f.read()
        safe_name = _safe_filename(f.filename)
        if len(content) > config.MAX_FILE_BYTES:
            results.append({"name": safe_name, "error": "File too large"})
            continue
        if _vault_storage_used() + len(content) > config.MAX_VAULT_BYTES:
            results.append({"name": safe_name, "error": "Vault storage full"})
            continue

        file_path = (target_dir / safe_name).resolve()
        if not str(file_path).startswith(str(config.VAULT_DIR.resolve())):
            results.append({"name": safe_name, "error": "Invalid filename"})
            continue
        file_path.write_bytes(content)
        results.append({"name": safe_name, "size": len(content)})

    return {"uploaded": results}


@app.get("/api/vault/download")
async def vault_download(path: str, token: str | None = None, session: str | None = Cookie(default=None)):
    _require_auth(session, token)
    target = _safe_vault_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@app.delete("/api/vault")
async def vault_delete(path: str, session: str | None = Cookie(default=None)):
    _require_auth(session)
    target = _safe_vault_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target == config.VAULT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Cannot delete vault root")

    if target.is_file():
        target.unlink()
        return {"ok": True, "deleted": "file"}
    elif target.is_dir():
        import shutil
        shutil.rmtree(target)
        return {"ok": True, "deleted": "directory"}


@app.post("/api/vault/mkdir")
async def vault_mkdir(path: str = Form(...), session: str | None = Cookie(default=None)):
    _require_auth(session)
    target = _safe_vault_path(path)
    if target.exists():
        raise HTTPException(status_code=409, detail="Already exists")
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": path}


# ── Clipboard ─────────────────────────────────────────

def _read_clip_meta(clip_id: str) -> dict | None:
    meta_path = CLIP_META_DIR / f"{clip_id}.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_clip_meta(clip_id: str, meta: dict):
    meta_path = CLIP_META_DIR / f"{clip_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _delete_clip(clip_id: str):
    meta = _read_clip_meta(clip_id)
    if meta:
        if meta.get("image_name"):
            (config.CLIPBOARD_DIR / meta["image_name"]).unlink(missing_ok=True)
        (CLIP_META_DIR / f"{clip_id}.json").unlink(missing_ok=True)


def _clip_storage_used() -> int:
    total = 0
    for meta_file in CLIP_META_DIR.glob("*.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        total += meta.get("size", 0)
    return total


def _clip_is_expired(meta: dict) -> bool:
    expire_at = meta.get("expire_at")
    if expire_at is None:
        return False
    return time.time() > expire_at


@app.get("/api/clipboard")
async def clip_list(session: str | None = Cookie(default=None)):
    _require_auth(session)
    clips = []
    for meta_file in sorted(CLIP_META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if _clip_is_expired(meta):
            _delete_clip(meta["id"])
            continue
        entry = {
            "id": meta["id"],
            "title": meta["title"],
            "type": meta["type"],
            "size": meta["size"],
            "created_at": meta["created_at"],
            "expire_at": meta.get("expire_at"),
        }
        if meta.get("expire_at") is not None:
            entry["remaining_seconds"] = round(meta["expire_at"] - time.time())
        else:
            entry["remaining_seconds"] = None
        clips.append(entry)
    clips.sort(key=lambda c: c["created_at"], reverse=True)
    return {"clips": clips, "storage_used": _clip_storage_used()}


@app.post("/api/clipboard")
async def clip_create(
    title: str = Form(...),
    clip_type: str = Form(default="text"),
    content: str = Form(default=""),
    expire_seconds: int = Form(default=86400),
    image: UploadFile | None = File(default=None),
    session: str | None = Cookie(default=None),
):
    _require_auth(session)

    clip_id = uuid.uuid4().hex[:12]
    now = time.time()
    expire_at = None if expire_seconds == 0 else now + expire_seconds

    if clip_type == "image":
        if not image or not image.filename:
            raise HTTPException(status_code=400, detail="Image file required")
        img_bytes = await image.read()
        if len(img_bytes) > config.MAX_CLIP_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
        if _clip_storage_used() + len(img_bytes) > config.MAX_CLIPBOARD_BYTES:
            raise HTTPException(status_code=400, detail="Clipboard storage full")
        ext = Path(image.filename).suffix or ".png"
        image_name = f"{clip_id}{ext}"
        (config.CLIPBOARD_DIR / image_name).write_bytes(img_bytes)
        meta = {
            "id": clip_id,
            "title": title,
            "type": "image",
            "content": None,
            "image_name": image_name,
            "size": len(img_bytes),
            "created_at": now,
            "expire_at": expire_at,
        }
    else:
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > config.MAX_CLIP_TEXT_BYTES:
            raise HTTPException(status_code=400, detail="Text too large (max 1MB)")
        if _clip_storage_used() + len(content_bytes) > config.MAX_CLIPBOARD_BYTES:
            raise HTTPException(status_code=400, detail="Clipboard storage full")
        meta = {
            "id": clip_id,
            "title": title,
            "type": "text",
            "content": content,
            "image_name": None,
            "size": len(content_bytes),
            "created_at": now,
            "expire_at": expire_at,
        }

    _write_clip_meta(clip_id, meta)
    return {"ok": True, "id": clip_id}


@app.get("/api/clipboard/{clip_id}")
async def clip_get(clip_id: str, session: str | None = Cookie(default=None)):
    _validate_file_id(clip_id)
    _require_auth(session)
    meta = _read_clip_meta(clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    if _clip_is_expired(meta):
        _delete_clip(clip_id)
        raise HTTPException(status_code=404, detail="Clip expired")
    result = {
        "id": meta["id"],
        "title": meta["title"],
        "type": meta["type"],
        "size": meta["size"],
        "created_at": meta["created_at"],
        "expire_at": meta.get("expire_at"),
    }
    if meta["type"] == "text":
        result["content"] = meta["content"]
    return result


@app.put("/api/clipboard/{clip_id}")
async def clip_update(
    clip_id: str,
    content: str = Form(default=""),
    session: str | None = Cookie(default=None),
):
    _validate_file_id(clip_id)
    _require_auth(session)
    meta = _read_clip_meta(clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    if _clip_is_expired(meta):
        _delete_clip(clip_id)
        raise HTTPException(status_code=404, detail="Clip expired")
    if meta["type"] != "text":
        raise HTTPException(status_code=400, detail="Only text clips can be updated")
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > config.MAX_CLIP_TEXT_BYTES:
        raise HTTPException(status_code=400, detail="Text too large (max 1MB)")
    old_size = meta["size"]
    new_size = len(content_bytes)
    if _clip_storage_used() - old_size + new_size > config.MAX_CLIPBOARD_BYTES:
        raise HTTPException(status_code=400, detail="Clipboard storage full")
    meta["content"] = content
    meta["size"] = new_size
    _write_clip_meta(clip_id, meta)
    return {"ok": True}


@app.delete("/api/clipboard/{clip_id}")
async def clip_delete(clip_id: str, session: str | None = Cookie(default=None)):
    _validate_file_id(clip_id)
    _require_auth(session)
    meta = _read_clip_meta(clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    _delete_clip(clip_id)
    return {"ok": True}


@app.delete("/api/clipboard")
async def clip_delete_all(session: str | None = Cookie(default=None)):
    _require_auth(session)
    count = 0
    for meta_file in list(CLIP_META_DIR.glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        _delete_clip(meta["id"])
        count += 1
    return {"deleted": count}


@app.get("/api/clipboard/{clip_id}/image")
async def clip_image(
    clip_id: str,
    token: str | None = None,
    session: str | None = Cookie(default=None),
):
    _validate_file_id(clip_id)
    _require_auth(session, token)
    meta = _read_clip_meta(clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    if _clip_is_expired(meta):
        _delete_clip(clip_id)
        raise HTTPException(status_code=404, detail="Clip expired")
    if meta["type"] != "image" or not meta.get("image_name"):
        raise HTTPException(status_code=400, detail="Not an image clip")
    file_path = config.CLIPBOARD_DIR / meta["image_name"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file missing")
    return FileResponse(path=str(file_path), filename=meta["image_name"])


# ── Frontend serving ──────────────────────────────────

@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>QuickDrop</h1><p>Frontend not found.</p>")
    return HTMLResponse(index.read_text(encoding="utf-8"))
