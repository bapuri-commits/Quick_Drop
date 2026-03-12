"""QuickDrop — Personal file cloud API (SyOps 계정 통합)."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import httpx
import jwt
from fastapi import (
    Cookie,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
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

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_FILE_ID_RE = re.compile(r"^[a-f0-9]{12}$")
JWT_ALGORITHM = "HS256"


# ── Auth ──────────────────────────────────────────────


def _decode_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, config.SYOPS_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _require_auth(
    syops_token: str | None = None,
    authorization: str | None = None,
    token: str | None = None,
) -> dict:
    """JWT 검증 후 {"user_id": int, "username": str, "role": str} 반환."""
    raw = syops_token or token
    if not raw and authorization and authorization.startswith("Bearer "):
        raw = authorization[7:]
    if not raw:
        raise HTTPException(status_code=401, detail="Unauthorized")
    payload = _decode_jwt(raw)
    return {
        "user_id": int(payload["sub"]),
        "username": payload.get("username", "unknown"),
        "role": payload.get("role", "user"),
    }


# ── Per-user directory helpers ────────────────────────


def _user_upload_dir(user_id: int) -> Path:
    d = config.UPLOAD_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_meta_dir(user_id: int) -> Path:
    d = _user_upload_dir(user_id) / ".meta"
    d.mkdir(exist_ok=True)
    return d


def _user_vault_dir(user_id: int) -> Path:
    d = config.VAULT_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_clipboard_dir(user_id: int) -> Path:
    d = config.CLIPBOARD_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_clip_meta_dir(user_id: int) -> Path:
    d = _user_clipboard_dir(user_id) / ".meta"
    d.mkdir(exist_ok=True)
    return d


# ── File meta helpers (user-scoped) ───────────────────


def _validate_file_id(file_id: str):
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID")


def _safe_filename(name: str | None) -> str:
    if not name:
        return "unnamed"
    safe = Path(name).name
    if not safe or safe in (".", ".."):
        return "unnamed"
    return safe


def _read_meta(user_id: int, file_id: str) -> dict | None:
    meta_path = _user_meta_dir(user_id) / f"{file_id}.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_meta(user_id: int, file_id: str, meta: dict):
    meta_path = _user_meta_dir(user_id) / f"{file_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _delete_file(user_id: int, file_id: str):
    meta = _read_meta(user_id, file_id)
    if meta:
        file_path = _user_upload_dir(user_id) / meta["stored_name"]
        file_path.unlink(missing_ok=True)
        (_user_meta_dir(user_id) / f"{file_id}.json").unlink(missing_ok=True)


def _total_storage_used(user_id: int) -> int:
    total = 0
    meta_dir = _user_meta_dir(user_id)
    for meta_file in meta_dir.glob("*.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        total += meta.get("size", 0)
    return total


# ── Auth endpoints ────────────────────────────────────


@app.get("/api/auth/check")
async def auth_check(syops_token: str | None = Cookie(default=None)):
    if not syops_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    info = _require_auth(syops_token=syops_token)
    return {"authenticated": True, "username": info["username"]}


# ── Files ─────────────────────────────────────────────


@app.get("/api/files")
async def list_files(syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    files = []
    for meta_file in sorted(_user_meta_dir(uid).glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        remaining = meta["expire_at"] - time.time()
        if remaining <= 0:
            _delete_file(uid, meta["id"])
            continue
        files.append({
            "id": meta["id"],
            "name": meta["original_name"],
            "size": meta["size"],
            "uploaded_at": meta["uploaded_at"],
            "expire_at": meta["expire_at"],
            "remaining_hours": round(remaining / 3600, 1),
            "from_user": meta.get("from_user"),
        })
    files.sort(key=lambda f: f["uploaded_at"], reverse=True)
    return {"files": files, "storage_used": _total_storage_used(uid), "username": user["username"]}


@app.post("/api/files")
async def upload_files(
    files: list[UploadFile] = File(...),
    expire_days: int = Form(default=config.DEFAULT_EXPIRE_DAYS),
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    expire_days = max(1, min(expire_days, 30))
    results = []

    for f in files:
        content = await f.read()
        if len(content) > config.MAX_FILE_BYTES:
            results.append({"name": f.filename, "error": "File too large"})
            continue
        if _total_storage_used(uid) + len(content) > config.MAX_STORAGE_BYTES:
            results.append({"name": f.filename, "error": "Storage full"})
            continue

        file_id = uuid.uuid4().hex[:12]
        ext = Path(f.filename or "file").suffix
        stored_name = f"{file_id}{ext}"
        file_path = _user_upload_dir(uid) / stored_name
        file_path.write_bytes(content)

        meta = {
            "id": file_id,
            "original_name": f.filename,
            "stored_name": stored_name,
            "size": len(content),
            "uploaded_at": time.time(),
            "expire_at": time.time() + expire_days * 86400,
            "owner_id": uid,
        }
        _write_meta(uid, file_id, meta)
        results.append({"name": f.filename, "id": file_id, "size": len(content)})

    return {"uploaded": results}


@app.get("/api/files/{file_id}/download")
async def download_file(
    file_id: str,
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    _validate_file_id(file_id)
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    meta = _read_meta(uid, file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    if time.time() > meta["expire_at"]:
        _delete_file(uid, file_id)
        raise HTTPException(status_code=404, detail="File expired")
    file_path = _user_upload_dir(uid) / meta["stored_name"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(
        path=str(file_path),
        filename=meta["original_name"],
        media_type="application/octet-stream",
    )


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str, syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    _validate_file_id(file_id)
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    meta = _read_meta(uid, file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    _delete_file(uid, file_id)
    return {"ok": True}


@app.delete("/api/files")
async def delete_all_files(syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    count = 0
    for meta_file in list(_user_meta_dir(uid).glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        _delete_file(uid, meta["id"])
        count += 1
    return {"deleted": count}


# ── File Send ─────────────────────────────────────────


class SendRequest(BaseModel):
    to_username: str


@app.post("/api/files/{file_id}/send")
async def send_file(
    file_id: str,
    body: SendRequest,
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    _validate_file_id(file_id)
    sender = _require_auth(syops_token, authorization)
    sender_uid = sender["user_id"]

    if body.to_username == sender["username"]:
        raise HTTPException(status_code=400, detail="Cannot send to yourself")

    meta = _read_meta(sender_uid, file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    if time.time() > meta["expire_at"]:
        _delete_file(sender_uid, file_id)
        raise HTTPException(status_code=404, detail="File expired")

    target_user = await _lookup_user(body.to_username, syops_token or "")
    target_uid = target_user["id"]

    src_path = _user_upload_dir(sender_uid) / meta["stored_name"]
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="File missing")

    new_file_id = uuid.uuid4().hex[:12]
    ext = Path(meta["stored_name"]).suffix
    new_stored = f"{new_file_id}{ext}"
    dst_path = _user_upload_dir(target_uid) / new_stored
    shutil.copy2(str(src_path), str(dst_path))

    new_meta = {
        "id": new_file_id,
        "original_name": meta["original_name"],
        "stored_name": new_stored,
        "size": meta["size"],
        "uploaded_at": time.time(),
        "expire_at": time.time() + 7 * 86400,
        "owner_id": target_uid,
        "from_user": sender["username"],
    }
    _write_meta(target_uid, new_file_id, new_meta)

    return {"ok": True, "sent_to": body.to_username, "new_file_id": new_file_id}


async def _lookup_user(username: str, token: str) -> dict:
    """SyOps API로 username → user 정보 조회."""
    url = f"{config.SYOPS_API_URL}/api/auth/users/{username}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to look up user")
    return resp.json()


# ── Vault (permanent storage) ─────────────────────────


def _safe_vault_path(user_id: int, path_str: str) -> Path:
    vault_root = _user_vault_dir(user_id)
    cleaned = path_str.strip("/").replace("\\", "/")
    resolved = (vault_root / cleaned).resolve()
    if not str(resolved).startswith(str(vault_root.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return resolved


def _vault_storage_used(user_id: int) -> int:
    total = 0
    vault_root = _user_vault_dir(user_id)
    for f in vault_root.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


@app.get("/api/vault")
async def vault_list(path: str = "/", syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    target = _safe_vault_path(uid, path)
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

    vault_root = _user_vault_dir(uid)
    rel = "/" + str(target.relative_to(vault_root.resolve())).replace("\\", "/")
    if rel == "/.":
        rel = "/"
    return {"path": rel, "items": items, "storage_used": _vault_storage_used(uid)}


@app.post("/api/vault")
async def vault_upload(
    files: list[UploadFile] = File(...),
    path: str = Form(default="/"),
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    target_dir = _safe_vault_path(uid, path)
    target_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for f in files:
        content = await f.read()
        safe_name = _safe_filename(f.filename)
        if len(content) > config.MAX_FILE_BYTES:
            results.append({"name": safe_name, "error": "File too large"})
            continue
        if _vault_storage_used(uid) + len(content) > config.MAX_VAULT_BYTES:
            results.append({"name": safe_name, "error": "Vault storage full"})
            continue

        vault_root = _user_vault_dir(uid)
        file_path = (target_dir / safe_name).resolve()
        if not str(file_path).startswith(str(vault_root.resolve())):
            results.append({"name": safe_name, "error": "Invalid filename"})
            continue
        file_path.write_bytes(content)
        results.append({"name": safe_name, "size": len(content)})

    return {"uploaded": results}


@app.get("/api/vault/download")
async def vault_download(
    path: str,
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    target = _safe_vault_path(uid, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@app.delete("/api/vault")
async def vault_delete(path: str, syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    target = _safe_vault_path(uid, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    vault_root = _user_vault_dir(uid)
    if target == vault_root.resolve():
        raise HTTPException(status_code=400, detail="Cannot delete vault root")

    if target.is_file():
        target.unlink()
        return {"ok": True, "deleted": "file"}
    elif target.is_dir():
        shutil.rmtree(target)
        return {"ok": True, "deleted": "directory"}


@app.post("/api/vault/mkdir")
async def vault_mkdir(path: str = Form(...), syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    target = _safe_vault_path(uid, path)
    if target.exists():
        raise HTTPException(status_code=409, detail="Already exists")
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": path}


# ── Clipboard ─────────────────────────────────────────


def _read_clip_meta(user_id: int, clip_id: str) -> dict | None:
    meta_path = _user_clip_meta_dir(user_id) / f"{clip_id}.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_clip_meta(user_id: int, clip_id: str, meta: dict):
    meta_path = _user_clip_meta_dir(user_id) / f"{clip_id}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _delete_clip(user_id: int, clip_id: str):
    meta = _read_clip_meta(user_id, clip_id)
    if meta:
        if meta.get("image_name"):
            (_user_clipboard_dir(user_id) / meta["image_name"]).unlink(missing_ok=True)
        (_user_clip_meta_dir(user_id) / f"{clip_id}.json").unlink(missing_ok=True)


def _clip_storage_used(user_id: int) -> int:
    total = 0
    for meta_file in _user_clip_meta_dir(user_id).glob("*.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        total += meta.get("size", 0)
    return total


def _clip_is_expired(meta: dict) -> bool:
    expire_at = meta.get("expire_at")
    if expire_at is None:
        return False
    return time.time() > expire_at


@app.get("/api/clipboard")
async def clip_list(syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    clips = []
    for meta_file in sorted(_user_clip_meta_dir(uid).glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if _clip_is_expired(meta):
            _delete_clip(uid, meta["id"])
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
    return {"clips": clips, "storage_used": _clip_storage_used(uid)}


@app.post("/api/clipboard")
async def clip_create(
    title: str = Form(...),
    clip_type: str = Form(default="text"),
    content: str = Form(default=""),
    expire_seconds: int = Form(default=86400),
    image: UploadFile | None = File(default=None),
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]

    clip_id = uuid.uuid4().hex[:12]
    now = time.time()
    expire_at = None if expire_seconds == 0 else now + expire_seconds

    if clip_type == "image":
        if not image or not image.filename:
            raise HTTPException(status_code=400, detail="Image file required")
        img_bytes = await image.read()
        if len(img_bytes) > config.MAX_CLIP_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
        if _clip_storage_used(uid) + len(img_bytes) > config.MAX_CLIPBOARD_BYTES:
            raise HTTPException(status_code=400, detail="Clipboard storage full")
        ext = Path(image.filename).suffix or ".png"
        image_name = f"{clip_id}{ext}"
        (_user_clipboard_dir(uid) / image_name).write_bytes(img_bytes)
        meta = {
            "id": clip_id,
            "title": title,
            "type": "image",
            "content": None,
            "image_name": image_name,
            "size": len(img_bytes),
            "created_at": now,
            "expire_at": expire_at,
            "owner_id": uid,
        }
    else:
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > config.MAX_CLIP_TEXT_BYTES:
            raise HTTPException(status_code=400, detail="Text too large (max 1MB)")
        if _clip_storage_used(uid) + len(content_bytes) > config.MAX_CLIPBOARD_BYTES:
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
            "owner_id": uid,
        }

    _write_clip_meta(uid, clip_id, meta)
    return {"ok": True, "id": clip_id}


@app.get("/api/clipboard/{clip_id}")
async def clip_get(clip_id: str, syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    _validate_file_id(clip_id)
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    meta = _read_clip_meta(uid, clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    if _clip_is_expired(meta):
        _delete_clip(uid, clip_id)
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
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    _validate_file_id(clip_id)
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    meta = _read_clip_meta(uid, clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    if _clip_is_expired(meta):
        _delete_clip(uid, clip_id)
        raise HTTPException(status_code=404, detail="Clip expired")
    if meta["type"] != "text":
        raise HTTPException(status_code=400, detail="Only text clips can be updated")
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > config.MAX_CLIP_TEXT_BYTES:
        raise HTTPException(status_code=400, detail="Text too large (max 1MB)")
    old_size = meta["size"]
    new_size = len(content_bytes)
    if _clip_storage_used(uid) - old_size + new_size > config.MAX_CLIPBOARD_BYTES:
        raise HTTPException(status_code=400, detail="Clipboard storage full")
    meta["content"] = content
    meta["size"] = new_size
    _write_clip_meta(uid, clip_id, meta)
    return {"ok": True}


@app.delete("/api/clipboard/{clip_id}")
async def clip_delete(clip_id: str, syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    _validate_file_id(clip_id)
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    meta = _read_clip_meta(uid, clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    _delete_clip(uid, clip_id)
    return {"ok": True}


@app.delete("/api/clipboard")
async def clip_delete_all(syops_token: str | None = Cookie(default=None), authorization: str | None = Header(default=None)):
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    count = 0
    for meta_file in list(_user_clip_meta_dir(uid).glob("*.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        _delete_clip(uid, meta["id"])
        count += 1
    return {"deleted": count}


@app.get("/api/clipboard/{clip_id}/image")
async def clip_image(
    clip_id: str,
    syops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    _validate_file_id(clip_id)
    user = _require_auth(syops_token, authorization)
    uid = user["user_id"]
    meta = _read_clip_meta(uid, clip_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Clip not found")
    if _clip_is_expired(meta):
        _delete_clip(uid, clip_id)
        raise HTTPException(status_code=404, detail="Clip expired")
    if meta["type"] != "image" or not meta.get("image_name"):
        raise HTTPException(status_code=400, detail="Not an image clip")
    file_path = _user_clipboard_dir(uid) / meta["image_name"]
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
