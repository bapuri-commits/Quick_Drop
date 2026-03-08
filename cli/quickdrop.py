#!/usr/bin/env python3
"""QuickDrop CLI — upload/download files from terminal."""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from http.cookiejar import MozillaCookieJar
import urllib.request

CONFIG_PATH = Path.home() / ".quickdrop.json"


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def api(cfg, method, path, data=None, headers=None, stream=False):
    url = urljoin(cfg["server"].rstrip("/") + "/", path.lstrip("/"))
    hdrs = {"Cookie": f"session={cfg.get('token', '')}"}
    if headers:
        hdrs.update(headers)

    if data is not None:
        req = Request(url, data=data, headers=hdrs, method=method)
    else:
        req = Request(url, headers=hdrs, method=method)

    try:
        resp = urlopen(req)
        if stream:
            return resp
        return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401:
            print("인증 만료. quickdrop login 을 다시 실행하세요.")
            sys.exit(1)
        body = e.read().decode("utf-8", errors="replace")
        print(f"오류 ({e.code}): {body}")
        sys.exit(1)


def multipart_encode(fields, files):
    boundary = "----QuickDropBoundary"
    body = b""
    for key, val in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f"{val}\r\n".encode()
    for key, (filename, content) in files:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def format_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1048576:
        return f"{n / 1024:.1f} KB"
    if n < 1073741824:
        return f"{n / 1048576:.1f} MB"
    return f"{n / 1073741824:.2f} GB"


# ── Commands ──────────────────────────────────────────

def cmd_login(args):
    server = args.server.rstrip("/")
    import getpass
    pw = getpass.getpass("비밀번호: ")

    body, ct = multipart_encode({"password": pw}, [])
    req = Request(
        f"{server}/api/auth",
        data=body,
        headers={"Content-Type": ct},
        method="POST",
    )
    try:
        resp = urlopen(req)
        data = json.loads(resp.read().decode("utf-8"))
        cfg = load_config()
        cfg["server"] = server
        cfg["token"] = data["token"]
        save_config(cfg)
        print(f"로그인 성공! 서버: {server}")
    except HTTPError as e:
        if e.code == 403:
            print("비밀번호가 틀렸습니다.")
        else:
            print(f"로그인 실패 ({e.code})")
        sys.exit(1)


def cmd_upload(args):
    cfg = load_config()
    if "server" not in cfg:
        print("먼저 quickdrop login <서버주소> 를 실행하세요.")
        sys.exit(1)

    file_data = []
    for path_str in args.files:
        p = Path(path_str)
        if not p.exists():
            print(f"파일 없음: {p}")
            continue
        file_data.append(("files", (p.name, p.read_bytes())))
        print(f"  준비: {p.name} ({format_size(p.stat().st_size)})")

    if not file_data:
        print("업로드할 파일이 없습니다.")
        return

    body, ct = multipart_encode({"expire_days": str(args.expire)}, file_data)
    result = api(cfg, "POST", "/api/files", data=body, headers={"Content-Type": ct})

    for item in result.get("uploaded", []):
        if "error" in item:
            print(f"  실패: {item['name']} — {item['error']}")
        else:
            print(f"  완료: {item['name']} ({format_size(item['size'])})")


def cmd_list(args):
    cfg = load_config()
    if "server" not in cfg:
        print("먼저 quickdrop login <서버주소> 를 실행하세요.")
        sys.exit(1)

    data = api(cfg, "GET", "/api/files")
    files = data["files"]
    if not files:
        print("파일이 없습니다.")
        return

    print(f"\n{'이름':<36} {'크기':>10}  {'남은 시간':>10}")
    print("─" * 60)
    for f in files:
        hrs = f["remaining_hours"]
        if hrs < 1:
            remaining = f"{hrs * 60:.0f}분"
        elif hrs < 24:
            remaining = f"{hrs:.0f}시간"
        else:
            remaining = f"{hrs / 24:.0f}일"
        name = f["name"][:34] + ".." if len(f["name"]) > 36 else f["name"]
        print(f"  {name:<34} {format_size(f['size']):>10}  {remaining:>8}")
    print(f"\n  총 {len(files)}개 · {format_size(data['storage_used'])} 사용 중")


def cmd_download(args):
    cfg = load_config()
    if "server" not in cfg:
        print("먼저 quickdrop login <서버주소> 를 실행하세요.")
        sys.exit(1)

    data = api(cfg, "GET", "/api/files")
    target = args.filename
    match = None
    for f in data["files"]:
        if f["name"] == target or f["id"] == target:
            match = f
            break

    if not match:
        print(f"파일을 찾을 수 없습니다: {target}")
        return

    resp = api(cfg, "GET", f"/api/files/{match['id']}/download", stream=True)
    out_path = Path(args.output) if args.output else Path(match["name"])
    with open(out_path, "wb") as fp:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            fp.write(chunk)
    print(f"  다운로드 완료: {out_path} ({format_size(out_path.stat().st_size)})")


def cmd_delete(args):
    cfg = load_config()
    if "server" not in cfg:
        print("먼저 quickdrop login <서버주소> 를 실행하세요.")
        sys.exit(1)

    if args.all:
        api(cfg, "DELETE", "/api/files")
        print("전체 삭제 완료.")
        return

    data = api(cfg, "GET", "/api/files")
    target = args.filename
    match = None
    for f in data["files"]:
        if f["name"] == target or f["id"] == target:
            match = f
            break

    if not match:
        print(f"파일을 찾을 수 없습니다: {target}")
        return

    api(cfg, "DELETE", f"/api/files/{match['id']}")
    print(f"  삭제 완료: {match['name']}")


# ── Vault commands ────────────────────────────────────

def _require_cfg():
    cfg = load_config()
    if "server" not in cfg:
        print("먼저 quickdrop login <서버주소> 를 실행하세요.")
        sys.exit(1)
    return cfg


def cmd_vault(args):
    vault_action = args.vault_command
    if not vault_action:
        print("사용법: quickdrop vault {list|upload|download|delete|mkdir}")
        return

    {"list": cmd_vault_list, "upload": cmd_vault_upload,
     "download": cmd_vault_download, "delete": cmd_vault_delete,
     "mkdir": cmd_vault_mkdir}[vault_action](args)


def cmd_vault_list(args):
    cfg = _require_cfg()
    path = args.path or "/"
    from urllib.parse import quote
    data = api(cfg, "GET", f"/api/vault?path={quote(path, safe='/')}")
    items = data["items"]

    print(f"\n  vault:{data['path']}")
    print("─" * 60)
    if not items:
        print("  (비어 있음)")
    else:
        for item in items:
            if item["type"] == "dir":
                print(f"  [D] {item['name']}/")
            else:
                print(f"  [F] {item['name']:<32} {format_size(item['size']):>10}")
    print(f"\n  vault 사용량: {format_size(data['storage_used'])}")


def cmd_vault_upload(args):
    cfg = _require_cfg()
    dest = args.dest or "/"

    file_data = []
    for path_str in args.files:
        p = Path(path_str)
        if not p.exists():
            print(f"파일 없음: {p}")
            continue
        file_data.append(("files", (p.name, p.read_bytes())))
        print(f"  준비: {p.name} ({format_size(p.stat().st_size)})")

    if not file_data:
        print("업로드할 파일이 없습니다.")
        return

    body, ct = multipart_encode({"path": dest}, file_data)
    result = api(cfg, "POST", "/api/vault", data=body, headers={"Content-Type": ct})

    for item in result.get("uploaded", []):
        if "error" in item:
            print(f"  실패: {item['name']} — {item['error']}")
        else:
            print(f"  완료: {item['name']} → vault:{dest}/{item['name']} ({format_size(item['size'])})")


def cmd_vault_download(args):
    cfg = _require_cfg()
    path = args.path
    from urllib.parse import quote
    resp = api(cfg, "GET", f"/api/vault/download?path={quote(path, safe='/')}", stream=True)

    filename = path.rstrip("/").split("/")[-1]
    out_path = Path(args.output) if args.output else Path(filename)
    with open(out_path, "wb") as fp:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            fp.write(chunk)
    print(f"  다운로드 완료: {out_path} ({format_size(out_path.stat().st_size)})")


def cmd_vault_delete(args):
    cfg = _require_cfg()
    path = args.path
    from urllib.parse import quote
    result = api(cfg, "DELETE", f"/api/vault?path={quote(path, safe='/')}")
    print(f"  삭제 완료: vault:{path} ({result.get('deleted', 'unknown')})")


def cmd_vault_mkdir(args):
    cfg = _require_cfg()
    path = args.path
    body, ct = multipart_encode({"path": path}, [])
    api(cfg, "POST", "/api/vault/mkdir", data=body, headers={"Content-Type": ct})
    print(f"  폴더 생성: vault:{path}")


# ── Clipboard commands ────────────────────────────────

EXPIRE_MAP = {
    "1h": 3600, "6h": 21600, "1d": 86400, "7d": 604800, "permanent": 0,
}


def cmd_clip(args):
    clip_action = args.clip_command
    if not clip_action:
        print("사용법: quickdrop clip {list|add|get|delete}")
        return
    {"list": cmd_clip_list, "add": cmd_clip_add,
     "get": cmd_clip_get, "delete": cmd_clip_delete}[clip_action](args)


def cmd_clip_list(args):
    cfg = _require_cfg()
    data = api(cfg, "GET", "/api/clipboard")
    clips = data["clips"]
    if not clips:
        print("클립보드가 비어있습니다.")
        return

    print(f"\n{'제목':<30} {'타입':>6}  {'크기':>10}  {'남은 시간':>10}")
    print("─" * 62)
    for c in clips:
        rs = c["remaining_seconds"]
        if rs is None:
            remaining = "영구"
        elif rs < 60:
            remaining = "곧 만료"
        elif rs < 3600:
            remaining = f"{rs // 60}분"
        elif rs < 86400:
            remaining = f"{rs // 3600}시간"
        else:
            remaining = f"{rs // 86400}일"
        title = c["title"][:28] + ".." if len(c["title"]) > 30 else c["title"]
        ctype = "이미지" if c["type"] == "image" else "텍스트"
        print(f"  {title:<28} {ctype:>6}  {format_size(c['size']):>10}  {remaining:>8}")
    print(f"\n  총 {len(clips)}개 · {format_size(data['storage_used'])} 사용 중")


def cmd_clip_add(args):
    cfg = _require_cfg()

    expire_str = args.expire or "1d"
    expire_seconds = EXPIRE_MAP.get(expire_str)
    if expire_seconds is None:
        print(f"잘못된 만료 옵션: {expire_str} (가능: 1h, 6h, 1d, 7d, permanent)")
        sys.exit(1)

    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"파일 없음: {p}")
            sys.exit(1)
        file_data = [("image", (p.name, p.read_bytes()))]
        fields = {
            "title": args.title,
            "clip_type": "image",
            "expire_seconds": str(expire_seconds),
            "content": "",
        }
        body, ct = multipart_encode(fields, file_data)
        print(f"  이미지 업로드: {p.name} ({format_size(p.stat().st_size)})")
    else:
        content = args.content or ""
        if not content and not sys.stdin.isatty():
            content = sys.stdin.read()
        if not content:
            print("내용이 필요합니다. 내용을 인자로 전달하거나 stdin으로 파이프하세요.")
            sys.exit(1)
        fields = {
            "title": args.title,
            "clip_type": "text",
            "expire_seconds": str(expire_seconds),
            "content": content,
        }
        body, ct = multipart_encode(fields, [])

    result = api(cfg, "POST", "/api/clipboard", data=body, headers={"Content-Type": ct})
    print(f"  추가 완료: \"{args.title}\" (id: {result['id']})")


def cmd_clip_get(args):
    cfg = _require_cfg()
    data = api(cfg, "GET", "/api/clipboard")
    target = args.title_or_id
    match = None
    for c in data["clips"]:
        if c["title"] == target or c["id"] == target:
            match = c
            break

    if not match:
        print(f"클립을 찾을 수 없습니다: {target}")
        return

    if match["type"] == "text":
        detail = api(cfg, "GET", f"/api/clipboard/{match['id']}")
        print(detail["content"])
    else:
        from urllib.parse import quote
        resp = api(cfg, "GET", f"/api/clipboard/{match['id']}/image", stream=True)
        out_path = Path(args.output) if args.output else Path(match["title"].replace("/", "_") + ".png")
        with open(out_path, "wb") as fp:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                fp.write(chunk)
        print(f"  이미지 저장: {out_path} ({format_size(out_path.stat().st_size)})")


def cmd_clip_delete(args):
    cfg = _require_cfg()
    if args.all:
        api(cfg, "DELETE", "/api/clipboard")
        print("클립보드 전체 삭제 완료.")
        return

    data = api(cfg, "GET", "/api/clipboard")
    target = args.title_or_id
    match = None
    for c in data["clips"]:
        if c["title"] == target or c["id"] == target:
            match = c
            break

    if not match:
        print(f"클립을 찾을 수 없습니다: {target}")
        return

    api(cfg, "DELETE", f"/api/clipboard/{match['id']}")
    print(f"  삭제 완료: {match['title']}")


# ── Main ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="quickdrop", description="QuickDrop CLI")
    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", help="서버 로그인")
    p_login.add_argument("server", help="서버 주소 (예: https://drop.example.com)")

    p_upload = sub.add_parser("upload", help="임시 파일 업로드 (drop)")
    p_upload.add_argument("files", nargs="+", help="업로드할 파일 경로")
    p_upload.add_argument("-e", "--expire", type=int, default=7, help="만료 일수 (기본 7)")

    p_list = sub.add_parser("list", help="임시 파일 목록 (drop)")

    p_dl = sub.add_parser("download", help="임시 파일 다운로드 (drop)")
    p_dl.add_argument("filename", help="파일 이름 또는 ID")
    p_dl.add_argument("-o", "--output", help="저장 경로")

    p_del = sub.add_parser("delete", help="임시 파일 삭제 (drop)")
    p_del.add_argument("filename", nargs="?", help="파일 이름 또는 ID")
    p_del.add_argument("--all", action="store_true", help="전체 삭제")

    # vault subcommand
    p_vault = sub.add_parser("vault", help="영구 보관 (vault)")
    vault_sub = p_vault.add_subparsers(dest="vault_command")

    pv_list = vault_sub.add_parser("list", help="폴더 목록")
    pv_list.add_argument("path", nargs="?", default="/", help="폴더 경로 (기본: /)")

    pv_upload = vault_sub.add_parser("upload", help="파일 업로드")
    pv_upload.add_argument("files", nargs="+", help="업로드할 파일 경로")
    pv_upload.add_argument("-d", "--dest", default="/", help="vault 대상 폴더 (기본: /)")

    pv_dl = vault_sub.add_parser("download", help="파일 다운로드")
    pv_dl.add_argument("path", help="vault 파일 경로 (예: /keys/id_rsa)")
    pv_dl.add_argument("-o", "--output", help="저장 경로")

    pv_del = vault_sub.add_parser("delete", help="파일/폴더 삭제")
    pv_del.add_argument("path", help="vault 경로")

    pv_mkdir = vault_sub.add_parser("mkdir", help="폴더 생성")
    pv_mkdir.add_argument("path", help="생성할 폴더 경로")

    # clip subcommand
    p_clip = sub.add_parser("clip", help="클립보드")
    clip_sub = p_clip.add_subparsers(dest="clip_command")

    pc_list = clip_sub.add_parser("list", help="클립보드 목록")

    pc_add = clip_sub.add_parser("add", help="클립보드 추가")
    pc_add.add_argument("title", help="클립 제목")
    pc_add.add_argument("content", nargs="?", default="", help="텍스트 내용 (생략 시 stdin)")
    pc_add.add_argument("-f", "--file", help="이미지 파일 경로")
    pc_add.add_argument("-e", "--expire", default="1d", help="만료 (1h|6h|1d|7d|permanent, 기본 1d)")

    pc_get = clip_sub.add_parser("get", help="클립 내용 조회")
    pc_get.add_argument("title_or_id", help="제목 또는 ID")
    pc_get.add_argument("-o", "--output", help="이미지 저장 경로")

    pc_del = clip_sub.add_parser("delete", help="클립 삭제")
    pc_del.add_argument("title_or_id", nargs="?", help="제목 또는 ID")
    pc_del.add_argument("--all", action="store_true", help="전체 삭제")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "login": cmd_login, "upload": cmd_upload, "list": cmd_list,
        "download": cmd_download, "delete": cmd_delete, "vault": cmd_vault,
        "clip": cmd_clip,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
