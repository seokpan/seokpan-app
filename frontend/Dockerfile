#
# 작성자: 최유준
# 작성 날짜: 2026-09-04 (v2 - PR #48 팀 확정 스펙 반영, 2026-09-04 재작성)
#
# [정정 2026-09-04, 실제 검증 중 발견] 최상단 `# syntax=docker/dockerfile:1` pragma
# 제거함. backend/Dockerfile과 동일 원인(외부 frontend nested 실행 실패) —
# 상세 근거는 backend/Dockerfile 주석 참고.
#
# [확정 스펙 출처] 팀원 메시지(PR #48 적용 값, 2026-09-04)
# - Build: Node.js 24.19.0, npm 12.0.2, npm ci && npm run build
# - Runtime Image: nginxinc/nginx-unprivileged:1.30.4-alpine3.24
# - Container Port: 8080
# - Health Endpoint: /health/live
# - SPA Fallback: try_files $uri $uri/ /index.html
# - Nginx PID·임시 파일 경로: /tmp 사용
# - readOnlyRootFilesystem 조건에서 실행 가능해야 함
# - /assets 장기 Cache + immutable, index.html은 재검증 Cache
# - Base Image는 Tag가 아닌 실제 Digest로 고정
#
# [Digest 확보] cicd 네임스페이스 kubectl run 개별 Pull 후 imageID 확인 (2026-09-04)
#   node:24.19.0-alpine                          -> sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43
#   nginxinc/nginx-unprivileged:1.30.4-alpine3.24 -> sha256:9b87ad3dd9f431c733f19dfb278c7eb3dba9dca381942c79818bb42f1a566a83
#
# [정정] v1의 nginx:1.27-alpine + sed로 포트/권한 수동 조정하던 방식은 폐기.
# nginx-unprivileged 이미지는 처음부터 non-root(uid 101)·8080 포트로 설계되어
# 별도 sed 트릭이 불필요해짐.

# ---------- Stage 1: build ----------
FROM node:24.19.0-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS builder

WORKDIR /app

# [정정 2026-09-04, 리뷰 반영] corepack enable(인자 없음)은 npm을 shim 대상에서
# 기본 제외한다 — npm은 Node.js에 이미 번들되어 있어, corepack 공식 문서가
# "npm shim은 명시적으로 요청하지 않으면 설치되지 않는다"고 명시함. 그 결과
# corepack prepare npm@12.0.2 --activate가 버전을 준비만 하고 실제 `npm` 커맨드는
# Node 24 이미지에 번들된 기본 npm(11.17.0)을 그대로 참조 — npm ci 시점
# EBADENGINE 경고로 발견됨. `corepack enable npm`으로 npm을 명시적으로 shim
# 대상에 포함시키고, npm --version 검증을 빌드 타임에 강제해 재발 방지.
RUN corepack enable npm && \
    corepack prepare npm@12.0.2 --activate && \
    npm --version | grep -qx '12.0.2' || (echo "ERROR: npm version mismatch, expected 12.0.2" && exit 1)

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---------- Stage 2: runtime (nginx-unprivileged) ----------
FROM nginxinc/nginx-unprivileged:1.30.4-alpine3.24@sha256:9b87ad3dd9f431c733f19dfb278c7eb3dba9dca381942c79818bb42f1a566a83 AS runtime

# nginx-unprivileged 이미지는 이미 non-root(uid 101, nginx 사용자)로 기본 구성됨.
# readOnlyRootFilesystem 대응: PID·caches·temp 전부 /tmp 하위로 재배치
# (컨테이너 파일시스템이 read-only여도 /tmp만 emptyDir로 쓰기 가능하면 기동됨 —
# 실제 Pod의 volumeMounts에 /tmp를 emptyDir로 마운트하는 건 GitOps Deployment 쪽 책임)
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

# curl 없을 수 있는 경량 이미지 전제 — wget은 alpine 계열에 기본 포함(BusyBox)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/health/live || exit 1

CMD ["nginx", "-g", "daemon off;"]
