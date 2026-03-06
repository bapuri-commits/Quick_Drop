# QuickDrop — VPS 배포 가이드

> DevOps 로드맵 Stage 2~5 실전 적용 프로젝트.

---

## Stage 2 — 수동 배포

```bash
# VPS에서 실행
cd /opt/apps
git clone https://github.com/<user>/QuickDrop.git quickdrop
cd quickdrop/backend

python3 -m venv /opt/envs/quickdrop
source /opt/envs/quickdrop/bin/activate
pip install -r requirements.txt

# 환경변수 설정
cp /opt/apps/quickdrop/.env.example /opt/envs/quickdrop.env
# 편집: QUICKDROP_PASSWORD, QUICKDROP_SECRET_KEY, QUICKDROP_UPLOAD_DIR=/opt/data/quickdrop/files
ln -s /opt/envs/quickdrop.env /opt/apps/quickdrop/.env

# 업로드 디렉토리 생성
mkdir -p /opt/data/quickdrop/files

# 실행
uvicorn main:app --host 0.0.0.0 --port 8200
```

## Stage 3 — Docker

```bash
cd /opt/apps/quickdrop
cp .env.example .env  # 편집
cd docker
docker compose up -d --build
```

## Stage 4 — nginx 리버스 프록시

```nginx
server {
    listen 80;
    server_name drop.도메인;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:8200;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Stage 5 — HTTPS

```bash
sudo certbot --nginx -d drop.도메인
```

## cron — 만료 파일 정리

```bash
# 매일 새벽 3시 만료 파일 정리
0 3 * * * cd /opt/apps/quickdrop/backend && /opt/envs/quickdrop/bin/python cleanup.py >> /opt/logs/quickdrop/cleanup.log 2>&1
```
