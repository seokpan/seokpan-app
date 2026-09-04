# syntax=docker/dockerfile:1
#
# 작성자: 최유준
# 작성 날짜: 2026-09-04
#
# 확정 근거:
#   - backend/pyproject.toml: requires-python == "3.13.15", uv required-version 0.12.5
#   - backend/README.md: uv run uvicorn seokpan.app:app --app-dir src --host 127.0.0.1 --port 8000
#     (컨테이너 내부이므로 host는 0.0.0.0으로 조정)
#   - backend/src/seokpan/health.py: APIRouter(prefix="/health", ...)
#
# [TODO/확인 필요] health.py의 정확한 하위 경로(예: "/health" 루트 GET인지, "/health/live"인지)는
# prefix만 확인된 상태 — HEALTHCHECK가 실패하면 이 부분부터 재확인할 것.
#
# Rootless BuildKit(buildctl-daemonless.sh)으로 빌드되는 것을 전제로 하며,
# Host Docker Socket을 사용하지 않는다(04 문서 11.1절 원칙).

# ---------- Stage 1: builder ----------
FROM python:3.13.15-slim-bookworm AS builder

# uv 0.12.5 바이너리만 공식 uv 이미지에서 가져옴 (Astral 권장 패턴)
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 의존성만 먼저 설치해 레이어 캐시 극대화 (소스 변경 시 재설치 방지)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# 실제 소스 복사 후 프로젝트 자체 설치
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.13.15-slim-bookworm AS runtime

# Rootless 원칙: non-root 사용자로 실행
RUN groupadd --gid 10001 seokpan && \
    useradd --uid 10001 --gid seokpan --shell /usr/sbin/nologin --create-home seokpan

WORKDIR /app

COPY --from=builder --chown=seokpan:seokpan /app/.venv /app/.venv
COPY --from=builder --chown=seokpan:seokpan /app/src /app/src

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER seokpan

EXPOSE 8000

# curl 미설치(slim 이미지) 전제 — Python 표준 라이브러리로 헬스체크
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["uvicorn", "seokpan.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
