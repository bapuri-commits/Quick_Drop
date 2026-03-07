"""QuickDrop — Personal file cloud API."""

import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi import (
    Cookie,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import config

app = FastAPI(title="QuickDrop", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

META_DIR = config.UPLOAD_DIR / ".meta"
META_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

TOKEN_LIFETIME = 60 * 60 * 24 * 30  # 30 days
_valid_tokens: dict[str, float] = {}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _require_auth(session: str | None):
    if not session or session not in _valid_tokens:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if time.time() - _valid_tokens[session] > TOKEN_LIFETIME:
        _valid_tokens.pop(session, None)
        raise HTTPException(status_code=401, detail="Session expired")


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
async def auth(response: Response, password: str = Form(...)):
    if password != config.PASSWORD:
        raise HTTPException(status_code=403, detail="Wrong password")
    token = uuid.uuid4().hex
    _valid_tokens[token] = time.time()
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="strict",
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
async def download_file(file_id: str, session: str | None = Cookie(default=None)):
    _require_auth(session)
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
        if len(content) > config.MAX_FILE_BYTES:
            results.append({"name": f.filename, "error": "File too large"})
            continue
        if _vault_storage_used() + len(content) > config.MAX_VAULT_BYTES:
            results.append({"name": f.filename, "error": "Vault storage full"})
            continue

        file_path = target_dir / f.filename
        file_path.write_bytes(content)
        results.append({"name": f.filename, "size": len(content)})

    return {"uploaded": results}


@app.get("/api/vault/download")
async def vault_download(path: str, session: str | None = Cookie(default=None)):
    _require_auth(session)
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


# ── Frontend serving ──────────────────────────────────

@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>QuickDrop</h1><p>Frontend not found.</p>")
    return HTMLResponse(index.read_text(encoding="utf-8"))
