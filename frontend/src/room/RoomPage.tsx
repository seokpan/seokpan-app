import { useEffect, useState, useSyncExternalStore } from "react";
import { useSession } from "../session/context";
import { useRoomConnection } from "./context";
import type { RoomView } from "./model";
import type { SnapshotStream } from "../realtime/stream";
import styles from "../styles/screens.module.css";
import { GamePanel } from "../game/GamePanel";
import gameStyles from "../game/game.module.css";
import { KickDialog } from "./KickDialog";
import { ChatPanel } from "../chat/ChatPanel";

export function RoomPage({ active = true }: { active?: boolean }) {
  const { stream } = useRoomConnection();
  return stream ? (
    <ConnectedRoom stream={stream} active={active} />
  ) : (
    <p role="status">방 연결을 준비하고 있습니다.</p>
  );
}
function ConnectedRoom({ stream, active }: { stream: SnapshotStream<RoomView>; active: boolean }) {
  const auth = useSession();
  const { dismissedResult, dismissResult } = useRoomConnection();
  const view = useSyncExternalStore(stream.subscribe, stream.getSnapshot);
  const room = view.snapshot?.room;
  const identity = auth.view.phase === "ready" ? auth.view.identity : null;
  const me = room?.participants.find((p) => p.participant_id === identity?.participant_id);
  const [kick, setKick] = useState<{
    id: string;
    version: number;
    trigger: HTMLButtonElement;
  } | null>(null);
  useEffect(() => {
    if (!active) setKick(null);
  }, [active]);
  const kickTarget =
    kick &&
    room?.state_version === kick.version &&
    room.status === "WAITING" &&
    room.owner_id === me?.participant_id &&
    me?.connected &&
    view.phase === "ready"
      ? room.participants.find(
          (p) => p.participant_id === kick.id && p.participant_id !== me.participant_id,
        )
      : null;
  const canChange =
    active && !auth.busy && view.phase === "ready" && room?.status === "WAITING" && me?.connected;
  const readyPlayers = room?.participants.filter((p) => p.ready) ?? [];
  const canStart =
    canChange &&
    room?.owner_id === me?.participant_id &&
    readyPlayers.length >= room.minimum_ready &&
    readyPlayers.some((p) => p.team === "BLACK") &&
    readyPlayers.some((p) => p.team === "WHITE");
  const showResult =
    room?.status === "WAITING" && room.last_game_id && room.last_game_id !== dismissedResult;
  const versioned = () => ({
    request_id: crypto.randomUUID(),
    expected_state_version: room!.state_version,
  });
  const path = () => ({ room_id: room!.room_id });
  const mutate = auth.mutate;
  return (
    <section className={`${styles.card} ${styles.roomCard}`} aria-labelledby="room-title">
      <div className={styles.sectionHeading}>
        <div>
          <p className="eyebrow">ROOM</p>
          <h1 id="room-title">{room?.name ?? "대기방"}</h1>
        </div>
        <button
          className={styles.secondaryButton}
          disabled={!room || view.phase !== "ready" || auth.busy}
          onClick={() =>
            void auth.run(
              () =>
                auth.api.request("/api/v1/rooms/{room_id}/participants/me", "delete", {
                  path: path(),
                  body: versioned(),
                }),
              "방에서 나왔습니다.",
            )
          }
        >
          방 나가기
        </button>
      </div>
      {view.phase !== "ready" && (
        <div role="status" className={styles.notice}>
          {view.message || "최신 방 상태를 확인하고 있습니다. 잠시 기다려 주세요."}
        </div>
      )}
      {(view.phase === "blocked" || view.phase === "disconnected" || view.phase === "ended") && (
        <>
          {view.snapshot && view.phase === "blocked" && (
            <button className={styles.secondaryButton} onClick={() => void stream.refresh()}>
              상태 다시 확인
            </button>
          )}{" "}
          <button className={styles.secondaryButton} onClick={stream.reconnect}>
            이 탭에서 다시 연결
          </button>
          <p className={styles.muted}>
            다시 연결은 기존 연결을 교체합니다. 다른 탭에서 이용 중이면 그 탭을 계속 사용해 주세요.
          </p>
        </>
      )}
      {room && (
        <>
          <p>
            {room.visibility === "PRIVATE" ? "비공개" : "공개"} · {room.participants.length} /{" "}
            {room.max_participants}명 · 최소 Ready {room.minimum_ready}명
          </p>
          {(room.status === "WAITING" || view.snapshot?.game) && (
            <GamePanel
              game={view.snapshot?.game ?? null}
              gameId={view.snapshot?.game?.game_id ?? room.last_game_id ?? ""}
              roomId={room.room_id}
              close={() => dismissResult(room.last_game_id!)}
              ready={view.phase === "ready"}
              refresh={stream.refresh}
              voteSeconds={room.vote_seconds}
              chat={
                <ChatPanel
                  roomId={room.room_id}
                  active={active}
                  enabled={!!me?.connected && (view.phase === "ready" || view.phase === "syncing")}
                />
              }
              waitingControls={
                room.status === "WAITING" && !showResult ? (
                  <>
                    {room.last_game_id && (
                      <button
                        className={styles.secondaryButton}
                        onClick={() => dismissResult(null)}
                      >
                        지난 판 결과 보기
                      </button>
                    )}
                    <section
                      className={`${gameStyles.infoPanel} ${styles.readyPanel}`}
                      aria-label="게임 시작 준비"
                    >
                      <h2>준비 현황</h2>
                      <p role="status">
                        Ready {readyPlayers.length}명 / 최소 {room.minimum_ready}명
                      </p>
                      <p className={styles.muted}>
                        흑팀 {readyPlayers.filter((p) => p.team === "BLACK").length}명 · 백팀{" "}
                        {readyPlayers.filter((p) => p.team === "WHITE").length}명 준비
                      </p>
                      <button
                        className={styles.primaryButton}
                        disabled={!canChange || me?.team === "NONE"}
                        onClick={() =>
                          void mutate(() =>
                            auth.api.request(
                              "/api/v1/rooms/{room_id}/participants/me/ready",
                              "put",
                              {
                                path: path(),
                                body: { ...versioned(), ready: !me?.ready },
                              },
                            ),
                          )
                        }
                      >
                        {me?.ready ? "Ready 취소" : "Ready"}
                      </button>
                      {me?.team === "NONE" && (
                        <p className={styles.muted}>아래에서 팀을 선택한 뒤 Ready를 눌러 주세요.</p>
                      )}
                      <label htmlFor="room-vote-seconds">투표 제한 시간</label>
                      <select
                        id="room-vote-seconds"
                        value={room.vote_seconds}
                        disabled={!canChange || me?.participant_id !== room.owner_id}
                        onChange={(event) => {
                          const vote_seconds = Number(event.target.value);
                          void mutate(() =>
                            auth.api.request("/api/v1/rooms/{room_id}/settings", "patch", {
                              path: path(),
                              body: { ...versioned(), vote_seconds },
                            }),
                          );
                        }}
                      >
                        {[5, 10, 15, 30].map((value) => (
                          <option key={value} value={value}>
                            {value}초
                          </option>
                        ))}
                      </select>
                      {me?.participant_id === room.owner_id ? (
                        <button
                          className={styles.primaryButton}
                          disabled={!canStart}
                          onClick={() =>
                            void mutate(() =>
                              auth.api.request("/api/v1/rooms/{room_id}/games", "post", {
                                path: path(),
                                body: versioned(),
                              }),
                            )
                          }
                        >
                          게임 시작
                        </button>
                      ) : (
                        <p className={styles.muted}>준비가 끝나면 방장이 게임을 시작합니다.</p>
                      )}
                      <details className={gameStyles.hint}>
                        <summary>시작 조건과 Ready 안내</summary>
                        <p>
                          최소 Ready 인원과 양 팀 각 1명 이상 Ready가 필요합니다. 시작할 때 Ready가
                          아닌 참가자는 이번 판을 관전합니다.
                        </p>
                        <p>
                          팀 변경 시 본인의 Ready가 해제됩니다. 방장 변경·투표 시간 변경 시에는 모두
                          해제됩니다.
                        </p>
                      </details>
                    </section>
                    <div className={styles.waitingTeams}>
                      {(["BLACK", "WHITE", "NONE"] as const).map((team) => (
                        <section key={team} className={styles.teamPanel}>
                          <h2>
                            {team === "BLACK"
                              ? "● 흑팀"
                              : team === "WHITE"
                                ? "○ 백팀"
                                : "팀 미선택"}
                          </h2>
                          <ul
                            aria-label={`${team === "BLACK" ? "흑팀" : team === "WHITE" ? "백팀" : "팀 미선택"} 참가자`}
                            tabIndex={0}
                          >
                            {room.participants
                              .filter((p) => p.team === team)
                              .sort((a, b) => a.joined_order - b.joined_order)
                              .map((p) => (
                                <li key={p.participant_id}>
                                  {p.display_name}
                                  {p.participant_id === room.owner_id ? " · 방장" : ""}
                                  {p.participant_id === me?.participant_id ? " · 나" : ""}
                                  <span>
                                    {" "}
                                    · {p.connected ? (p.ready ? "Ready" : "미준비") : "연결 끊김"}
                                  </span>
                                  {room.owner_id === me?.participant_id &&
                                    p.participant_id !== me.participant_id && (
                                      <button
                                        className={styles.secondaryButton}
                                        disabled={!canChange}
                                        aria-label={`${p.display_name} 강퇴`}
                                        onClick={(event) =>
                                          setKick({
                                            id: p.participant_id,
                                            version: room.state_version,
                                            trigger: event.currentTarget,
                                          })
                                        }
                                      >
                                        강퇴
                                      </button>
                                    )}
                                </li>
                              ))}
                          </ul>
                          <button
                            className={styles.secondaryButton}
                            disabled={!canChange || me?.team === team}
                            onClick={() =>
                              void mutate(() =>
                                auth.api.request(
                                  "/api/v1/rooms/{room_id}/participants/me/team",
                                  "put",
                                  {
                                    path: path(),
                                    body: { ...versioned(), team },
                                  },
                                ),
                              )
                            }
                          >
                            {team === "NONE"
                              ? "팀 선택 해제"
                              : team === "BLACK"
                                ? "흑팀 선택"
                                : "백팀 선택"}
                          </button>
                        </section>
                      ))}
                    </div>
                  </>
                ) : undefined
              }
            />
          )}
        </>
      )}
      {active && kick && kickTarget && (
        <KickDialog
          name={kickTarget.display_name}
          busy={auth.busy}
          trigger={kick.trigger}
          close={() => setKick(null)}
          confirm={() => {
            if (auth.busy) return;
            const selected = kick;
            void mutate(() =>
              auth.api.request(
                "/api/v1/rooms/{room_id}/participants/{participant_id}/kick",
                "post",
                {
                  path: { room_id: room!.room_id, participant_id: selected.id },
                  body: {
                    request_id: crypto.randomUUID(),
                    expected_state_version: selected.version,
                  },
                },
              ),
            ).finally(() => setKick(null));
          }}
        />
      )}
    </section>
  );
}
