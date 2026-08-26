# seokpan-app

석판팀 「石나가는 판단」 1차 프로젝트의 애플리케이션 소스코드를 관리하는 Repository입니다.

## Repository Structure

```text
seokpan-app/
├── frontend/
├── backend/
├── .gitignore
└── README.md
```

## Responsibility

### Frontend

사용자에게 제공되는 웹 UI 영역을 담당합니다.

주요 범위는 로그인·회원가입, 로비, 방 생성·입장, 대기방, 흑·백 팀 선택 및 Ready, 15×15 오목판, 투표 현황, 게임 결과, 채팅과 실시간 화면 갱신입니다.

Frontend의 실제 프로젝트 Scaffold와 세부 디렉터리 구조는 구현 기술과 개발 방식이 확정된 후 `frontend/` 아래에 구성합니다.

### Backend

Python / FastAPI 기반 Backend 애플리케이션을 담당합니다.

주요 범위는 인증, 방 관리, 게임 진행, 투표 처리, 오목·Renju 규칙, WebSocket 기반 실시간 통신, 재접속·상태 복구, Redis 연계, MariaDB 연계, Member 전적·Rating입니다.

Backend의 세부 모듈 구조는 실제 구현 착수 시 `backend/` 아래에 구성합니다.

## Repository Boundary

이 Repository는 애플리케이션 소스코드와 애플리케이션 자체 테스트를 관리합니다.

Kubernetes Desired State는 `seokpan-gitops`에서 관리하며, Host·VM·Network·Kubernetes Bootstrap 등 인프라 자동화는 `seokpan-infra`에서 관리합니다.

## Security

Password, Token, Private Key, 실제 `.env` 값 등 민감정보는 Repository에 저장하지 않습니다.

