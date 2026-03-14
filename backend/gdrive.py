"""Google Drive API 래퍼 — 서비스 계정 기반 파일 업로드."""

from __future__ import annotations

import logging
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
log = logging.getLogger("gdrive")


class GDriveUploader:
    def __init__(self, service_account_path: str):
        creds = Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES
        )
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def upload_file(
        self,
        local_path: str,
        folder_id: str,
        filename: str | None = None,
    ) -> dict:
        """파일을 Google Drive 폴더에 업로드하고 파일 정보를 반환."""
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {local_path}")

        file_metadata: dict = {
            "name": filename or path.name,
            "parents": [folder_id],
        }
        media = MediaFileUpload(str(path), resumable=True)
        result = (
            self.service.files()
            .create(body=file_metadata, media_body=media, fields="id,name,webViewLink")
            .execute()
        )
        log.info("Uploaded %s → Drive id=%s", path.name, result.get("id"))
        return result

    def find_or_create_folder(self, name: str, parent_id: str) -> str:
        """하위 폴더를 찾거나 없으면 생성. 폴더 ID 반환."""
        query = (
            f"name='{name}' and '{parent_id}' in parents "
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
