"""Google Drive OAuth 2.0 인증 스크립트 (1회 실행).

사용법:
  1. GCP Console에서 OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
  2. credentials.json 다운로드하여 이 파일과 같은 폴더에 배치
  3. 실행: python gdrive_auth.py
  4. 브라우저에서 Google 로그인 + 권한 허용
  5. 생성된 gdrive-token.json을 VPS에 업로드:
     scp gdrive-token.json dev@46.250.251.82:/opt/envs/gdrive-token.json
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "gdrive-token.json"


def main():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("토큰 갱신 중...")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"오류: {CREDENTIALS_FILE} 파일이 없습니다.")
                print()
                print("GCP Console에서 OAuth 2.0 클라이언트 ID를 생성하고")
                print("credentials.json을 다운로드하여 이 폴더에 넣으세요.")
                print()
                print("경로: API 및 서비스 → 사용자 인증 정보 → OAuth 2.0 클라이언트 ID")
                print("유형: 데스크톱 앱")
                return

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
            print("인증 완료!")

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        print(f"토큰 저장: {TOKEN_FILE}")

    print()
    print("다음 단계:")
    print(f"  scp {TOKEN_FILE} dev@46.250.251.82:/opt/envs/gdrive-token.json")
    print("  ssh dev@46.250.251.82 'chmod 600 /opt/envs/gdrive-token.json'")


if __name__ == "__main__":
    main()
