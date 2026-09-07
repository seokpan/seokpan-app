# seokpan-app

석판팀 1차 프로젝트의 **Frontend / Backend Application Source Code**를 관리하는 Repository입니다.

실시간 투표 기반 오목 게임 서비스를 구현하며, Application Domain과 HTTP / WebSocket API, Database / Redis Adapter, Container 및 CI/CD 연계를 관리합니다.

Kubernetes Desired State는 `seokpan-gitops`, Host / VM / Network / Kubernetes Bootstrap은 `seokpan-infra`에서 관리합니다.

---

## Application Overview

서비스의 전체 Application 흐름은 다음과 같습니다.

```text
Client
  │
  ▼
Frontend
  │
  ├──────── HTTP ────────┐
  │                      │
  └──── WebSocket ───────┤
                         ▼
                      Backend
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
           MariaDB                Redis
         Authority Data         Runtime State
```

Application은 **MariaDB를 영속 데이터의 권위 저장소**, **Redis를 Runtime State 및 실시간 상태 관리 저장소**로 구분하여 사용합니다.

---

## Repository Structure

```text
seokpan-app/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── pull_request_template.md
│
├── docs/
│   └── mvp-implementation-baseline.md
│
├── backend/
│   ├── src/
│   │   └── seokpan/
│   ├── tests/
│   ├── docs/
│   ├── migrations/
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── uv.lock
│   └── README.md
│
├── frontend/
│   ├── src/
│   ├── tests/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── package-lock.json
│   └── README.md
│
├── .editorconfig
├── .gitattributes
├── .gitignore
└── README.md
```

---

# Frontend

Frontend는 사용자가 직접 사용하는 Web UI를 담당합니다.

기술 구성:

* React
* TypeScript
* Vite
* npm
* Nginx

주요 기능 영역:

```text
Authentication
   │
   ▼
Lobby
   │
   ▼
Room
   │
   ├── Team / Ready
   │
   ▼
Game
   │
   ├── Board
   ├── Vote
   ├── Result
   └── Chat / Realtime Update
```

주요 화면:

* 로그인 / 회원가입
* Lobby
* Room 생성 / 입장
* Waiting Room
* Team / Ready
* 15 × 15 오목 Board
* Vote
* Game Result
* Chat
* 실시간 상태 갱신

Frontend는 Server가 관리하는 Snapshot / Event를 기준으로 화면 상태를 구성하며, WebSocket은 상태 변경 명령을 직접 처리하는 RPC 용도로 사용하지 않습니다.

---

# Backend

Backend는 Python / FastAPI 기반 Modular Monolith로 구성합니다.

기술 구성:

* CPython 3.13
* FastAPI
* SQLAlchemy
* Alembic
* asyncmy
* redis-py
* Pydantic
* Ruff
* mypy
* pytest

Backend의 주요 책임:

```text
Authentication / Session
        │
        ▼
Lobby / Room
        │
        ▼
Game / Vote / Turn
        │
        ├── Domain Rules
        │
        ├── MariaDB Persistence
        │
        └── Redis Runtime
        │
        ▼
WebSocket Event
```

---

## Domain

핵심 게임 규칙은 외부 Provider와 분리된 Pure Domain에서 관리합니다.

주요 Domain:

* Room
* Game / Board / Renju
* Vote / Turn
* Result / Forfeit / Rating

Domain은 다음 구현체에 직접 의존하지 않는 것을 원칙으로 합니다.

```text
FastAPI
SQLAlchemy
Redis Client
Database Driver
External Provider
```

Application Layer가 Domain과 Provider 사이를 연결합니다.

---

## Persistence

### MariaDB

MariaDB는 Application의 영속 데이터 권위 저장소입니다.

현재 Application에서 관리하는 주요 데이터:

```text
member
member_stats
game
game_participant
move
game_result
rating_history
```

주요 책임:

* Member Identity
* Game 기록
* Participant Snapshot
* 공식 Move
* Game Result
* Member Statistics
* Rating
* Rating History

Database Schema 변경은 Alembic Migration으로 관리합니다.

---

### Redis

Redis는 Application의 Runtime State를 관리합니다.

주요 영역:

```text
Session
Room
Connection
Game
Vote
Resolver
Event / Runtime State
```

Redis는 Move / Result / Rating의 영속적인 권위 저장소가 아닙니다.

```text
MariaDB
  └── 공식 영속 데이터

Redis
  └── 현재 Runtime State
```

Redis Runtime과 MariaDB Persistence 사이의 책임 경계를 유지합니다.

---

# HTTP API

HTTP API는 Application 상태 변경 및 조회를 담당합니다.

기본 API Prefix:

```text
/api/v1
```

주요 영역:

```text
Session
  └── Guest / Member Authentication

Room
  ├── Room List
  ├── Create / Join
  ├── Team / Ready
  └── Leave / Settings

Game
  ├── Start
  ├── Snapshot
  └── Result

Vote
  ├── Register
  ├── Replace
  └── Delete
```

상태 변경 요청에는 필요에 따라 다음 보호机制를 사용합니다.

* Session Cookie
* Origin / Referer
* CSRF Token
* `request_id`
* `expected_state_version`

동일 요청의 재처리와 오래된 상태를 구분하여 처리합니다.

---

# WebSocket

실시간 상태 전달은 다음 경계를 사용합니다.

```text
/ws/v1
```

주요 Endpoint:

```text
/ws/v1/lobby
/ws/v1/rooms/{room_id}
```

WebSocket은 다음을 담당합니다.

* Lobby Snapshot
* Room Snapshot
* Game / Vote Event
* Connection Recovery
* 실시간 상태 변경 알림

기본 흐름:

```text
WebSocket Connect
      │
      ▼
Authentication / Authorization
      │
      ▼
Snapshot
      │
      ▼
Event Stream
      │
      ├── State Change
      ├── Game Event
      └── Vote Event
```

상태 변경 명령 자체는 HTTP 및 Backend 내부 Runner가 담당합니다.

---

# Game Flow

Application의 핵심 게임 흐름은 다음과 같습니다.

```text
Member / Guest
      │
      ▼
Room Join
      │
      ▼
Team / Ready
      │
      ▼
Game Start
      │
      ▼
Turn
      │
      ▼
Vote
      │
      ▼
Resolver
      │
      ├── Move
      │
      └── Pass
      │
      ▼
Game Result
      │
      ▼
Rating / Statistics
      │
      ▼
Room WAITING
```

게임 종료 후에도 Room 자체는 유지할 수 있으며, 종료된 Game의 결과와 다음 Game 시작을 구분하여 처리합니다.

---

# Application State Responsibility

Application의 상태는 다음 기준으로 분리합니다.

| State                | Owner   |
| -------------------- | ------- |
| Member Identity      | MariaDB |
| Game / Move / Result | MariaDB |
| Rating / History     | MariaDB |
| Session              | Redis   |
| Room Runtime         | Redis   |
| Connection State     | Redis   |
| Current Vote         | Redis   |
| Resolver Runtime     | Redis   |
| Final Game Result    | MariaDB |

Redis 상태가 변경되더라도 공식 영속 데이터와의 책임 경계를 유지합니다.

---

# Implementation Progress

Application 구현은 단계적으로 진행합니다.

```text
A-01  MVP Implementation Baseline
   │
   ▼
A-02  Application Scaffold
   │
   ▼
A-03  Pure Domain
   │
   ├── Room
   ├── Board / Renju
   ├── Vote / Turn
   └── Result / Rating
   │
   ▼
A-04  MariaDB
   │
   ├── Model / Alembic
   └── Persistence Adapter
   │
   ▼
A-05  Redis
   │
   ├── Session
   ├── Room
   └── Vote / Resolver
   │
   ▼
A-06  HTTP / WebSocket
   │
   ▼
A-07  Headless First Success
   │
   ▼
A-08  Frontend First Success
   │
   ▼
A-09  Container / Jenkins
   │
   ▼
A-10  Provider / GitOps Integration
```

현재 `main`은 Pure Domain, MariaDB / Redis Adapter, HTTP / WebSocket 흐름과 Headless First Success까지 구현이 진행된 상태입니다.

실제 MariaDB / Redis / Kubernetes Provider 검증과 Application Runtime 통합은 별도의 Integration Gate로 구분합니다.

---

# Headless First Success

현재 Application의 Headless 검증은 Browser 없이 Backend의 전체 흐름을 검증하는 단계입니다.

```text
Session
  ↓
Room
  ↓
Game
  ↓
Vote
  ↓
Turn Resolution
  ↓
Move / Pass
  ↓
Game Result
  ↓
Next Game
```

이 검증은 Application 내부의 Domain / Application / Fake Provider 연결을 확인하기 위한 것입니다.

따라서:

```text
Fake Provider Success
        ≠
Real Provider Integration Success
```

실제 MariaDB, Redis, Kubernetes, Gateway 환경의 First Success는 별도의 Integration 단계에서 검증합니다.

---

# Container

Backend와 Frontend는 각각 독립적인 Container Image로 구성합니다.

```text
Backend
  └── backend/Dockerfile
       └── FastAPI / Uvicorn

Frontend
  └── frontend/Dockerfile
       └── Nginx
```

Application Container는 다음 환경을 기준으로 합니다.

* Linux Container
* Non-root
* 고정 Base Image
* 고정 Dependency
* Health Endpoint
* Runtime Credential 외부 주입

Migration은 일반 Backend Startup 과정에서 자동 실행하지 않습니다.

---

# CI/CD Integration

Application의 CI/CD는 다음 흐름으로 연결됩니다.

```text
Git Commit
    │
    ▼
Jenkins
    │
    ├── Backend Test
    ├── Frontend Test
    ├── Image Build
    │
    ▼
Harbor
    │
    ▼
Image Digest
    │
    ▼
seokpan-gitops
    │
    ▼
Argo CD
    │
    ▼
Kubernetes
```

Application Repository는 **Cluster에 직접 배포하는 책임을 가지지 않습니다.**

Jenkins Pipeline의 최종 배포 산출물은 GitOps 변경으로 연결하고, 실제 Cluster 적용은 Argo CD가 담당합니다.

---

# Repository Boundary

```text
seokpan-app
    │
    ├── Application Source
    ├── Domain
    ├── API
    ├── Database Model / Migration
    ├── Redis Adapter
    ├── Tests
    └── Container
          │
          ▼
    seokpan-gitops
          │
          └── Kubernetes Desired State
                  │
                  ▼
             Kubernetes

seokpan-infra
    │
    └── Host / VM / Network /
        Kubernetes Bootstrap /
        Infrastructure Automation
```

### Responsibility

| Repository       | Responsibility                                  |
| ---------------- | ----------------------------------------------- |
| `seokpan-app`    | Application Source / Test / Container           |
| `seokpan-gitops` | Kubernetes Desired State / Argo CD              |
| `seokpan-infra`  | Infrastructure / Kubernetes Bootstrap / Ansible |
| `seokpan-docs`   | Architecture / Design / Project Documentation   |

---

# Development & Validation

## Backend

```text
uv sync --locked
      ↓
Ruff
      ↓
mypy
      ↓
pytest
```

## Frontend

```text
npm ci
  ↓
TypeScript
  ↓
Vitest
  ↓
Vite Build
```

## Integration

```text
Application Test
      ↓
Container
      ↓
Jenkins / BuildKit
      ↓
Harbor
      ↓
GitOps
      ↓
Argo CD
      ↓
Kubernetes
```

각 단계의 성공은 다음 단계의 성공과 동일하게 취급하지 않습니다.

---

# Documentation

Application 세부 구현은 각 영역의 문서를 참고합니다.

```text
docs/
└── mvp-implementation-baseline.md

backend/docs/
├── member-identity.md
├── mariadb-baseline.md
├── game-persistence.md
├── redis-session.md
├── redis-room-runtime.md
├── redis-vote-runtime.md
├── lobby-room-http.md
├── lobby-room-websocket.md
├── turn-resolution-runner.md
└── headless-first-success.md
```

Project-wide Architecture와 공용 설계 기준은 `seokpan-docs`에서 관리합니다.

---

# Security

Repository에는 다음 민감정보를 저장하지 않습니다.

* Password
* Session Token
* CSRF Token
* Private Key
* Database Credential
* Redis Credential
* 실제 `.env`
* Kubernetes Secret 값

Application은 Runtime에서 필요한 Credential을 외부에서 주입받는 것을 기본으로 합니다.

특히 Migration Credential은 일반 Application Runtime과 분리합니다.

---

# Collaboration Workflow

```text
Issue
  │
  ▼
Branch
  │
  ▼
Implementation
  │
  ▼
Test
  │
  ▼
Commit
  │
  ▼
Pull Request
  │
  ▼
Review
  │
  ▼
Squash Merge
```

구현 결과는 정적 검증, Headless 검증, 실제 Provider 검증을 구분하여 기록합니다.

---

# Related Repositories

### Infrastructure

`seokpan-infra`

Host / VM / Network / Kubernetes Bootstrap 및 Infrastructure Automation

### GitOps

`seokpan-gitops`

Kubernetes Desired State와 Argo CD 배포 구성

### Documentation

`seokpan-docs`

프로젝트 요구사항, Logical / Physical Architecture, 설계 결정 및 변경 이력

---

# Current Principle

Application 구현에서는 다음 원칙을 유지합니다.

1. **Domain과 Provider를 분리합니다.**
2. **MariaDB와 Redis의 책임을 분리합니다.**
3. **HTTP와 WebSocket의 책임을 분리합니다.**
4. **Fake Provider 검증과 실제 Provider 검증을 구분합니다.**
5. **Application Source와 Kubernetes Desired State를 분리합니다.**
6. **Migration Credential과 Runtime Credential을 분리합니다.**
7. **GitOps Repository를 통한 Kubernetes 배포를 사용합니다.**
8. **검증되지 않은 Runtime 통합을 완료 상태로 표시하지 않습니다.**
