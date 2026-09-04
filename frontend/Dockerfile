# syntax=docker/dockerfile:1
#
# 작성자: 최유준
# 작성 날짜: 2026-09-04
#
# 확정 근거:
#   - frontend/package.json scripts.build = "tsc -b && vite build" (Vite, 산출물은 dist/)
#   - frontend/package.json scripts.test = "vitest run"
#
# [TODO/확인 필요] Node 버전은 package.json에 engines 필드가 없어 확정하지 못함 —
# 여기서는 Node 20 LTS를 기본값으로 가정. 팀 표준 버전이 따로 있으면 교체할 것.
# [TODO/확인 필요] React Router 등 클라이언트 사이드 라우팅 사용 여부 미확인 —
# 아래 nginx.conf는 SPA 전제로 모든 경로를 index.html로 fallback 처리함.
# 순수 정적 페이지(라우팅 없음)라면 이 fallback은 불필요하나 두어도 무해함.

# ---------- Stage 1: build ----------
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---------- Stage 2: runtime (nginx) ----------
FROM nginx:1.27-alpine AS runtime

# Rootless 원칙: nginx:alpine은 nginx-unprivileged 계열이 아니므로
# non-root 실행을 위해 최소 설정 조정 (포트 8080, 필요한 디렉토리 권한 이전)
RUN sed -i 's/listen\s*80;/listen 8080;/' /etc/nginx/conf.d/default.conf && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/cache/nginx /var/run/nginx.pid /etc/nginx/conf.d

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
