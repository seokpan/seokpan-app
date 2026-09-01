# Backend

「石나가는 판단」 서비스의 Backend 애플리케이션 영역입니다.

CPython 3.13.15와 FastAPI 0.141.1을 기반으로 인증, 방 관리, 게임 진행, 투표 처리, 오목·Renju 규칙, WebSocket 기반 실시간 통신, 재접속·상태 복구 및 Redis·MariaDB 연계를 담당합니다.

하나의 배포 단위를 유지하는 Modular Monolith로 구현하고, Domain이 Framework나 Provider Client를 직접 참조하지 않도록 Ports/Adapters 경계를 사용합니다. Project와 Dependency Lock은 `uv`, 품질 Gate는 pytest 계열·Ruff·mypy strict를 사용합니다.

현재 Application Factory, Settings와 Health Endpoint Scaffold에 Room, Game 및 Vote·Turn Pure Domain을 추가하고 있습니다. Room Domain은 방·참가자·팀·Ready·방장 승계·시작 조건을 판단합니다. Game Domain은 15×15 Board·좌표·Move·Renju 금수·승패에 더해 확정 이탈 기반 몰수·시스템 무효 종료와 Elo 갱신 계획을 담당합니다. Vote Domain은 현재 팀의 최종 유효표, 마감 후보, 외부 동률 선택 입력, Pass와 연속 무투표 공동 패배를 외부 Provider 없이 판단합니다. 다른 기능 Domain과 Provider Adapter는 관련 Issue에서 필요한 만큼 추가합니다. HTTP·WebSocket, 상태, Redis·MariaDB 기준은 [Application MVP 구현 기준](../docs/mvp-implementation-baseline.md)을 따릅니다.

MariaDB는 기존 7개 Table을 [v1 Alembic Baseline](docs/mariadb-baseline.md)으로 채택합니다. Identity와 Game Runtime Credential은 분리하고 Migration Credential은 정상 Backend 실행에서 격리합니다. 실제 Runtime DB 적용은 별도 Provider 검토·승인을 거칩니다.

## Development

uv 0.12.5를 사용하며 `pyproject.toml`의 `required-version`으로 다른 버전의 실행을 거부합니다. Windows 전역 uv를 프로젝트 기준으로 사용하지 않으며, 실제 Linux Container·CI에서도 uv 0.12.5를 명시적으로 설치해 같은 Lock을 사용합니다.

```powershell
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Room Pure Domain의 첫 Branch Coverage Ratchet은 다음 명령으로 검증합니다.

```powershell
uv run pytest --cov=seokpan.room.domain --cov-branch --cov-report=term-missing --cov-fail-under=100
```

Game Pure Domain은 다음 명령으로 같은 Ratchet을 적용합니다.

```powershell
uv run pytest --cov=seokpan.game.domain --cov-branch --cov-report=term-missing --cov-fail-under=100
```

Vote·Turn Pure Domain은 다음 명령으로 같은 Ratchet을 적용합니다.

```powershell
uv run pytest --cov=seokpan.vote.domain --cov-branch --cov-report=term-missing --cov-fail-under=100
```

개발 서버는 다음 명령으로 실행합니다.

```powershell
uv run uvicorn seokpan.app:app --app-dir src --host 127.0.0.1 --port 8000
```

Windows 검증은 Linux Container·MariaDB·Redis·Kubernetes 통합 완료를 의미하지 않습니다.
