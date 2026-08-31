# Frontend

「石나가는 판단」 서비스의 사용자 웹 UI 영역입니다.

Frontend는 로그인·회원가입, 로비, 방 생성·입장, 대기방, 팀 선택·Ready, 오목판, 투표 현황, 게임 결과, 채팅 및 실시간 화면 갱신 등 사용자 인터페이스를 담당합니다.

React 19.2.8, TypeScript 5.9.3 strict, Vite 8.2.2와 npm Lock을 사용합니다. 서버 Snapshot과 Event를 권위 상태로 사용하고 로컬 UI 상태와 분리하며, Event 누락·재접속 시 Snapshot으로 다시 수렴합니다.

현재 Scaffold는 최소 Router, 기본 화면, CSS Token과 Component Test만 포함합니다. 서비스 화면과 서버 권위 상태 처리는 관련 First Success 작업에서 추가합니다. HTTP·WebSocket과 상태 처리 기준은 [Application MVP 구현 기준](../docs/mvp-implementation-baseline.md)을 따릅니다.

추후 제공되는 UX/UI Mockup은 방향성 참고자료이며 공식 요구사항이나 Pixel-perfect 명세로 취급하지 않습니다.

## Development

Node.js 24 LTS와 npm 12를 사용합니다.

```powershell
npm ci
npm run typecheck
npm test
npm run build
npm audit
```

개발 서버는 `npm run dev`로 실행합니다. Windows 검증은 Linux/Nginx·Gateway·Kubernetes 통합 완료를 의미하지 않습니다.
