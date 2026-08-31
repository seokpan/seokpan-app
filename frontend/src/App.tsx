import { Link, Route, Routes } from "react-router-dom";

function BootstrapPage() {
  return (
    <main className="app-shell">
      <p className="eyebrow">SEOKPAN MVP</p>
      <h1>石나가는 판단</h1>
      <p>Application Scaffold가 준비되었습니다.</p>
      <p className="scope-note">
        서비스 기능과 UX/UI는 이후 Domain·First Success 작업에서 구현합니다.
      </p>
    </main>
  );
}

function NotFoundPage() {
  return (
    <main className="app-shell">
      <h1>페이지를 찾을 수 없습니다.</h1>
      <Link to="/">시작 화면으로 돌아가기</Link>
    </main>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<BootstrapPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
