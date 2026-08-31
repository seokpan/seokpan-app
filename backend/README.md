# Backend

「石나가는 판단」 서비스의 Backend 애플리케이션 영역입니다.

CPython 3.13.15와 FastAPI 0.141.1을 기반으로 인증, 방 관리, 게임 진행, 투표 처리, 오목·Renju 규칙, WebSocket 기반 실시간 통신, 재접속·상태 복구 및 Redis·MariaDB 연계를 담당합니다.

하나의 배포 단위를 유지하는 Modular Monolith로 구현하고, Domain이 Framework나 Provider Client를 직접 참조하지 않도록 Ports/Adapters 경계를 사용합니다. Project와 Dependency Lock은 `uv`, 품질 Gate는 pytest 계열·Ruff·mypy strict를 사용합니다.

세부 모듈 구조와 Lock은 별도 Scaffold 작업에서 이 디렉터리 아래에 구성합니다. HTTP·WebSocket, 상태, Redis·MariaDB 기준은 [Application MVP 구현 기준](../docs/mvp-implementation-baseline.md)을 따릅니다.
