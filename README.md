# QuickDrop

데스크탑, 노트북, 핸드폰 사이에서 파일을 빠르게 주고받는 개인용 미니 클라우드.

카톡 나에게 보내기, 메일 나에게 보내기를 대체한다.

## 기능

- **웹 UI** — 드래그앤드롭 업로드, 파일 목록, 다운로드, 삭제 (모바일 반응형, 다크모드)
- **CLI** — `quickdrop upload`, `quickdrop download` 등 터미널에서 빠른 파일 전송
- **자동 만료** — 1~30일 설정, 기본 7일 후 자동 삭제
- **단일 비밀번호** — 개인용 심플 인증

## 빠른 시작 (로컬)

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # 비밀번호 설정

uvicorn main:app --port 8200
# http://localhost:8200 접속
```

## CLI 사용법

```bash
python cli/quickdrop.py login http://localhost:8200
python cli/quickdrop.py upload report.pdf screenshot.png
python cli/quickdrop.py list
python cli/quickdrop.py download report.pdf
python cli/quickdrop.py delete report.pdf
```

## Docker

```bash
cp .env.example .env        # 비밀번호 설정
cd docker
docker compose up -d --build
# http://localhost:8200 접속
```

## 기술 스택

- Python 3.12, FastAPI, uvicorn
- Vanilla HTML/CSS/JS (단일 파일, 프레임워크 없음)
- Docker, nginx (VPS 배포)

## 구조

```
QuickDrop/
  backend/          # FastAPI 서버
  frontend/         # 웹 UI (index.html)
  cli/              # CLI 클라이언트
  docker/           # Dockerfile + docker-compose.yml
  docs/             # 배포 가이드, 핸드오프
```
