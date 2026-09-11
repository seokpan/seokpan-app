#
# 작성자: 최유준
# 작성 날짜: 2026-09-04 (v2 - PR #48 팀 확정 스펙 반영, 2026-09-04 재작성)
#
# [확정 스펙 출처] 팀원 메시지(PR #48 적용 값, 2026-09-04)
# - Base Image: python:3.13.15-slim-trixie
# - uv: 0.12.5
# - 실행 명령: uvicorn seokpan.app:app --app-dir src --host 0.0.0.0 --port 8000
# - Container Port: 8000
# - HEALTHCHECK: /health/live
# - UID/GID: 10001
# - Runtime Image 포함 대상: /app/src, /app/.venv, /app/alembic.ini, /app/migrations
#   (migrations/audit/도 제외하지 않음)
# - Migration은 Image에 실행 자산만 포함, 기본 CMD에서는 실행하지 않음
# - Base Image는 Tag가 아닌 실제 Digest로 고정 (latest/Major·Minor 이동 Tag 금지)
#
# [Digest 확보 방법] cicd 네임스페이스에서 kubectl run으로 개별 Pull 후
# .status.containerStatuses[0].imageID 확인 (2026-09-04)
#   python:3.13.15-slim-trixie  -> sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
#   ghcr.io/astral-sh/uv:0.12.5 -> sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1
#
# [PR #48 범위 밖] /metrics 구현, DB·Redis 실제 연결, Alembic 자동 실행,
# Image 내 Credential/Private Key 포함, 임의 Replica/Resource 결정 — 전부 미포함.
#
# Rootless BuildKit(buildctl-daemonless.sh)으로 빌드되는 것을 전제로 하며,
# Host Docker Socket을 사용하지 않는다(04 문서 11.1절 원칙).
#
# [수정, 2026-09-11] Trivy Scan CRITICAL/HIGH 차단 정책 대응 (Issue #58).
# runtime 스테이지에 perl-base 제거(CVE-2026-13221/42496/8376, CRITICAL,
# Fixed Version 없음) + setuptools/msgpack 조건부 업그레이드(HIGH, Fixed
# Version 있음) 추가. 상세 근거는 해당 RUN 라인 바로 위 주석 참고.

# ---------- Stage 1: builder ----------
FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS builder

COPY --from=ghcr.io/astral-sh/uv@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 의존성만 먼저 설치해 레이어 캐시 극대화 (소스 변경 시 재설치 방지)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# 실제 소스·Alembic 자산 복사 후 프로젝트 자체 설치
# (migrations/audit/ 포함 — 팀 확정 스펙에 따라 제외하지 않음)
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.13.15-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime

# Rootless 원칙: non-root 사용자로 실행 (UID/GID 10001 고정)
RUN groupadd --gid 10001 seokpan && \
    useradd --uid 10001 --gid seokpan --shell /usr/sbin/nologin --create-home seokpan

WORKDIR /app

COPY --from=builder --chown=seokpan:seokpan /app/.venv /app/.venv
COPY --from=builder --chown=seokpan:seokpan /app/src /app/src
COPY --from=builder --chown=seokpan:seokpan /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=seokpan:seokpan /app/migrations /app/migrations

# [추가, 2026-09-11] Trivy Scan CRITICAL/HIGH 차단 정책 대응.
#
# 1) perl-base 제거 (CVE-2026-13221/42496/8376, CRITICAL, Fixed Version 없음)
#    앱이 Perl을 전혀 사용하지 않고, python:3.13.15-slim-trixie 베이스에
#    Debian 기본 패키지로 딸려온 것일 뿐이라 애초에 이미지에 있을 필요가
#    없음. "임의 예외처리 금지" 정책과 무관 — 예외를 넣는 게 아니라
#    불필요한 걸 제거하는 것. Essential 패키지라 제거 시 apt/dpkg 저수준
#    도구가 깨질 수 있으나, 이 runtime 단계는 이후 apt/dpkg를 전혀 쓰지
#    않으므로 안전할 것으로 판단 — 다만 실제 빌드로 반드시 재검증할 것.
#
# 2) setuptools/msgpack 조건부 업그레이드 (HIGH, Fixed Version 있음:
#    setuptools>=78.1.1, msgpack>=1.2.1). 이 둘은 uv sync가 설치한 27개
#    패키지 목록(.venv)에 없어 베이스 이미지 시스템 site-packages 쪽으로
#    추정됨 — 정확한 출처는 미확인. 무조건 설치하면 원래 없던 패키지를
#    새로 추가하는 역효과가 날 수 있어, "이미 import 가능한 경우에만"
#    업그레이드하도록 조건부 처리.
RUN apt-get update && \
    apt-get purge -y --auto-remove perl-base 2>/dev/null; \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    (python3 -c "import setuptools" 2>/dev/null && pip install --no-cache-dir --upgrade "setuptools>=78.1.1" || true) && \
    (python3 -c "import msgpack" 2>/dev/null && pip install --no-cache-dir --upgrade "msgpack>=1.2.1" || true)

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER seokpan

EXPOSE 8000

# curl 미설치(slim 이미지) 전제 — Python 표준 라이브러리로 헬스체크
# 경로는 /health/live (health.py 실제 라우트, 팀 확정 스펙과 일치)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')" || exit 1

# 기본 CMD는 애플리케이션 서버만 기동 — Migration(seokpan-migration-gate)은
# 여기 포함하지 않음. Image에는 실행 자산(alembic.ini, migrations/)만 포함되어
# 필요 시 별도로 `seokpan-migration-gate` 명령을 명시적으로 실행해야 함.
CMD ["uvicorn", "seokpan.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
