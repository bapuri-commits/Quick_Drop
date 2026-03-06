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
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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


# ── Main ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="quickdrop", description="QuickDrop CLI")
    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", help="서버 로그인")
    p_login.add_argument("server", help="서버 주소 (예: https://drop.example.com)")

    p_upload = sub.add_parser("upload", help="파일 업로드")
    p_upload.add_argument("files", nargs="+", help="업로드할 파일 경로")
    p_upload.add_argument("-e", "--expire", type=int, default=7, help="만료 일수 (기본 7)")

    p_list = sub.add_parser("list", help="파일 목록")

    p_dl = sub.add_parser("download", help="파일 다운로드")
    p_dl.add_argument("filename", help="파일 이름 또는 ID")
    p_dl.add_argument("-o", "--output", help="저장 경로")

    p_del = sub.add_parser("delete", help="파일 삭제")
    p_del.add_argument("filename", nargs="?", help="파일 이름 또는 ID")
    p_del.add_argument("--all", action="store_true", help="전체 삭제")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"login": cmd_login, "upload": cmd_upload, "list": cmd_list,
     "download": cmd_download, "delete": cmd_delete}[args.command](args)


if __name__ == "__main__":
    main()
