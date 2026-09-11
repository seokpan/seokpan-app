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

# [수정, 2026-09-11 2차] 1차 시도가 조용히 실패한 것을 확인.
#
# 1) perl-base: apt는 Essential 패키지 제거 시 -y만으로는 부족하고
#    --allow-remove-essential이 추가로 필요함(없으면 경고만 찍고 실제로는
#    제거 안 됨 — 1차 빌드 로그에 "Removing perl-base..." 실행 로그가
#    없었던 것으로 확인). 또한 실패를 `2>/dev/null;`로 숨기고 다음 단계로
#    넘어가게 했던 게 "조용한 실패"를 감춘 원인이라 제거 — 이제 실패하면
#    빌드 자체가 실패하도록 함(의도대로 안 지워지면 바로 드러나야 함).
#
# 2) setuptools/msgpack: "import 가능하면 업그레이드" 조건이 1차에서
#    False로 나온 것으로 보임(Trivy 결과가 1차와 동일) — 정확한 설치
#    위치를 특정 못 한 상태. 추측으로 또 고치는 대신, 실제 위치를 빌드
#    로그에 출력하는 진단 커맨드로 먼저 교체함. 이 결과를 보고 다음
#    수정을 정확하게 할 것.
RUN apt-get update && \
    apt-get purge -y --allow-remove-essential --auto-remove perl-base && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    echo "=== msgpack/setuptools 실제 위치 진단 ===" && \
    (find / -xdev \( -iname "*msgpack*" -o -iname "*setuptools*" \) 2>/dev/null | grep -v '^/proc' || echo "(파일 없음)") && \
    echo "=== python3 사이트 패키지 경로 ===" && \
    python3 -c "import sys; print('\n'.join(sys.path))"

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
