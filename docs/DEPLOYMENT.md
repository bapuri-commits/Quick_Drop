# QuickDrop — VPS 배포 가이드

> DevOps 로드맵 Stage 2~5 실전 적용 프로젝트.
> 도메인: `syworkspace.cloud` (가비아), 서브도메인: `drop.syworkspace.cloud`
>
> **참조**: nginx/systemd 설정의 중앙 관리 사본은 `SyOps/deploy/`에 있습니다.
> DevOps 로드맵 전체는 `SyOps/docs/DEVOPS_ROADMAP.md`를 참조하세요.

---

## Stage 2 — 수동 배포 ✅

```bash
# VPS에서 실행
cd /opt/apps
git clone https://github.com/bapuri-commits/Quick_Drop.git quickdrop
cd quickdrop/backend

python3 -m venv /opt/apps/quickdrop/.venv
source /opt/apps/quickdrop/.venv/bin/activate
pip install -r requirements.txt

# 환경변수 설정
cp /opt/apps/quickdrop/.env.example /opt/envs/quickdrop.env
# 편집: QUICKDROP_PASSWORD, QUICKDROP_SECRET_KEY 등
ln -s /opt/envs/quickdrop.env /opt/apps/quickdrop/.env

# 데이터 디렉토리 생성
mkdir -p /opt/data/quickdrop/{drop,vault}

# systemd 서비스
sudo systemctl enable --now quickdrop
```

## Stage 4 — nginx 리버스 프록시 ✅

설정 파일: `/etc/nginx/sites-available/services`

```nginx
server {
    server_name drop.syworkspace.cloud;
    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:8200;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # SSL managed by Certbot
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/drop.syworkspace.cloud/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/drop.syworkspace.cloud/privkey.pem;
}
```

## Stage 5 — HTTPS ✅

```bash
# 인증서 발급 (certbot이 nginx 설정 자동 수정)
sudo certbot --nginx -d drop.syworkspace.cloud -d news.syworkspace.cloud -d syworkspace.cloud

# 인증서 만료: 2026-06-06 (자동 갱신 설정됨)
sudo certbot renew --dry-run  # 갱신 테스트
```

## cron — 만료 파일 정리 ✅

```bash
# 매일 UTC 03:00 만료 파일 자동 정리
0 3 * * * cd /opt/apps/quickdrop/backend && /opt/apps/quickdrop/.venv/bin/python cleanup.py >> /opt/logs/quickdrop/cleanup.log 2>&1
```

## 운영 명령어

```bash
# 서비스 관리
sudo systemctl status/restart quickdrop
sudo systemctl status/restart/reload nginx

# 로그
sudo journalctl -u quickdrop -f
cat /opt/logs/quickdrop/cleanup.log

# nginx 설정 변경 시
sudo nginx -t && sudo systemctl reload nginx

# 코드 업데이트
cd /opt/apps/quickdrop && git pull && sudo systemctl restart quickdrop

# SSL 인증서 확인
sudo certbot certificates
```

## 접속 URL

| 서비스 | URL |
|--------|-----|
| QuickDrop | `https://drop.syworkspace.cloud` |
| News_Agent 브리핑 | `https://news.syworkspace.cloud` |
| 루트 도메인 | `https://syworkspace.cloud` → drop으로 리다이렉트 |

## CLI 설정

```bash
python quickdrop.py login --server https://drop.syworkspace.cloud
```
