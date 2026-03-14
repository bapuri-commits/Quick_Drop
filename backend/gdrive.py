"""Google Drive API 래퍼 — OAuth 2.0 기반 파일 업로드."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
log = logging.getLogger("gdrive")


def _load_credentials(token_path: str) -> Credentials:
    """저장된 토큰 파일에서 자격증명을 로드하고, 만료 시 자동 갱신."""
    path = Path(token_path)
    if not path.exists():
        raise FileNotFoundError(
            f"토큰 파일이 없습니다: {token_path}\n"
            "gdrive_auth.py를 실행하여 인증을 완료하세요."
        )

    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
        log.info("OAuth token refreshed")

    return creds


class GDriveUploader:
    def __init__(self, token_path: str):
        creds = _load_credentials(token_path)
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def upload_file(
        self,
        local_path: str,
        folder_id: str,
        filename: str | None = None,
    ) -> dict:
        """파일을 Google Drive 폴더에 업로드. 동일 이름 파일이 있으면 덮어쓴다."""
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {local_path}")

        name = filename or path.name
        existing_id = self._find_file(name, folder_id)

        media = MediaFileUpload(str(path), resumable=True)

        if existing_id:
            result = (
                self.service.files()
                .update(fileId=existing_id, media_body=media, fields="id,name,webViewLink")
                .execute()
            )
            log.info("Updated %s (id=%s)", name, existing_id)
        else:
            file_metadata: dict = {"name": name, "parents": [folder_id]}
            result = (
                self.service.files()
                .create(body=file_metadata, media_body=media, fields="id,name,webViewLink")
                .execute()
            )
            log.info("Uploaded %s → Drive id=%s", name, result.get("id"))

        return result

    def _find_file(self, name: str, folder_id: str) -> str | None:
        """폴더 내 동일 이름 파일의 ID를 반환. 없으면 None."""
        safe_name = name.replace("'", "\\'")
        query = (
            f"name='{safe_name}' and '{folder_id}' in parents "
            f"and mimeType!='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        results = self.service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def find_or_create_folder(self, name: str, parent_id: str) -> str:
        """하위 폴더를 찾거나 없으면 생성. 폴더 ID 반환."""
        safe_name = name.replace("'", "\\'")
        query = (
            f"name='{safe_name}' and '{parent_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        results = self.service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]

        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = self.service.files().create(body=metadata, fields="id").execute()
        log.info("Created Drive folder '%s' id=%s", name, folder["id"])
        return folder["id"]

    def list_files(self, folder_id: str) -> list[dict]:
        """폴더 내 파일 목록 조회."""
        query = f"'{folder_id}' in parents and trashed=false"
        results = (
            self.service.files()
            .list(
                q=query,
                fields="files(id,name,size,createdTime,webViewLink)",
                orderBy="createdTime desc",
            )
            .execute()
        )
        return results.get("files", [])
