# seokpan-app

석판팀 「石나가는 판단」 1차 프로젝트의 애플리케이션 소스코드를 관리하는 Repository입니다.

## Repository Structure

```text
seokpan-app/
├── docs/
│   └── mvp-implementation-baseline.md
├── frontend/
├── backend/
├── .gitignore
└── README.md
```

## Responsibility

### Frontend

사용자에게 제공되는 웹 UI 영역을 담당합니다.

주요 범위는 로그인·회원가입, 로비, 방 생성·입장, 대기방, 흑·백 팀 선택 및 Ready, 15×15 오목판, 투표 현황, 게임 결과, 채팅과 실시간 화면 갱신입니다.

Frontend는 React·TypeScript·Vite와 npm Lock을 사용하며 Nginx 정적 Application으로 제공합니다. Scaffold와 세부 구조는 별도 작업에서 `frontend/` 아래에 구성합니다.

### Backend

Python / FastAPI 기반 Backend 애플리케이션을 담당합니다.

주요 범위는 인증, 방 관리, 게임 진행, 투표 처리, 오목·Renju 규칙, WebSocket 기반 실시간 통신, 재접속·상태 복구, Redis 연계, MariaDB 연계, Member 전적·Rating입니다.

Backend는 CPython·FastAPI Modular Monolith와 Ports/Adapters 경계를 사용합니다. Scaffold와 세부 모듈 구조는 별도 작업에서 `backend/` 아래에 구성합니다.

## Implementation Baseline

Application의 HTTP·WebSocket, 상태 전이, MariaDB·Redis 책임, 재접속과 오류 처리 기준은 [`docs/mvp-implementation-baseline.md`](docs/mvp-implementation-baseline.md)에서 관리합니다.

구현 순서는 구현 기준 → Scaffold → Pure Domain·Fake Adapter → Headless First Success → 실제 Provider → Frontend → Container·Jenkins·GitOps 통합입니다. Fake 또는 Windows Test의 성공을 실제 Provider·Linux Container·Kubernetes 통합 완료로 표시하지 않습니다.

## Repository Boundary

이 Repository는 애플리케이션 소스코드와 애플리케이션 자체 테스트를 관리합니다.

Kubernetes Desired State는 `seokpan-gitops`에서 관리하며, Host·VM·Network·Kubernetes Bootstrap 등 인프라 자동화는 `seokpan-infra`에서 관리합니다.

## Security

Password, Token, Private Key, 실제 `.env` 값 등 민감정보는 Repository에 저장하지 않습니다.

Windows Host에서 개발하지만 Source와 실행 자산은 UTF-8·LF 및 Linux Container 실행을 기준으로 관리합니다.
