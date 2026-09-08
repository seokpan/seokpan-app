// 작성자: 최유준
// 작성 날짜: 2026-09-08
//
// seokpan-app 표준 Jenkinsfile (Issue #40, PR-54)
//
// 범위(확정, 리뷰 반영 후 재확정):
//   - 이 Jenkinsfile은 PR Pipeline(Backend/Frontend Test + Build 검증)까지만 포함한다.
//     Harbor Push 금지, GitOps 변경 금지, 실제 DB/Redis Credential 사용 금지.
//   - main Merge 이후 Image Pipeline(Build & Push, SBOM/Scan, Promote, Verify Push)은
//     이번 PR 범위에서 완전히 제외하고 별도 Issue(#58)에서 작업한다.
//     -> PR-54 리뷰에서 "PR Pipeline으로 범위를 좁혔다는 설명과 Diff에 main Stage가
//        남아있는 것이 불일치한다"는 지적을 받아, main Stage 코드 자체를 이번 PR에서
//        제거함. main Stage 재작업 시 #58에서 아래 항목을 반드시 함께 반영할 것:
//          1) Guard: HTTP 200(exit 1) / 404(정상 진행) / 그 외(exit 1, fail-closed)로
//             명시적으로 분기할 것. 현재 초안 로직은 200 아니면 전부 통과시키는 결함이
//             있었음(401/403/5xx/파싱 실패 시에도 Push 단계로 진행되는 문제).
//          2) main 브랜치에서도 Backend/Frontend Lint & Test(P1 검증)를 재실행할 것
//             (PR 시점 검증과 main 시점 사이에 다른 PR이 먼저 merge되었을 수 있음).
//          3) 후보 태그(-scanning) 재실행 시 Harbor Tag Immutability Rule 저촉 여부
//             확인 및 재실행 정책 문서화.
//          4) 최신 Issue #40 P2 요구사항(non-root 검증, Health Smoke Test, SBOM,
//             Provenance, Scan, 최종 Digest, main Commit-Jenkins Build/Run ID 연결
//             기록)을 #58 완료 기준에 전부 포함할 것.
//
// 환경 제약(확정):
//   - Agent Label: buildkit-rootless
//   - DOCKER_CONFIG: /home/user/.docker (harbor-robot-dockerconfig Secret, Opaque 타입, key: config.json)
//   - Harbor CA: /etc/buildkit/certs/ca.crt (SSL_CERT_FILE 경유로 buildctl-daemonless.sh에 신뢰)
//   - buildkit 컨테이너 내 사용 가능 도구: git, wget(BusyBox), sed 만 존재.
//     curl / kustomize / jq 없음, non-root라 apk install도 불가.
//
// [선행 조건] 이 Jenkinsfile은 buildkit-rootless PodTemplate에 'python', 'node' 컨테이너가
// 추가되어 있어야 동작합니다(container('python'), container('node') 참조).
// -> seokpan-gitops PR #41(merge 완료), PR #43(imagePullSecrets, merge 완료)로 선행 조건 충족됨.

pipeline {
    agent {
        kubernetes {
            label 'buildkit-rootless'
        }
    }

    environment {
        DOCKER_CONFIG  = '/home/user/.docker'
        REGISTRY_HOST  = 'harbor.seokpan.soldesk.store'
        HARBOR_PROJECT = 'seokpan'
        HOME           = '/tmp'
        // SSL_CERT_FILE은 여기서 전역 지정하지 않음. buildkit 컨테이너는 Harbor 내부 CA를
        // 신뢰해야 하지만, python/node 컨테이너에 이 값이 새어 들어가면 uv/npm이 PyPI/npm
        // registry 같은 퍼블릭 CA 사이트를 "UnknownIssuer"로 거부하게 됨(실제 재현된 장애).
        // 그래서 buildkit을 쓰는 Stage에서만 withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt'])
        // 로 국소 적용한다.
    }

    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    stages {

        stage('Checkout') {
            steps {
                script {
                    def scmVars = checkout scm
                    env.GIT_COMMIT_SHA = scmVars.GIT_COMMIT
                }
            }
        }

        // ------------------------------------------------------------------
        // PR Pipeline: Test + Build 검증까지만. (이 Jenkinsfile의 전체 범위)
        // ------------------------------------------------------------------

        stage('Backend: Lint & Test') {
            steps {
                dir('backend') {
                    container('python') {
                        sh 'uv lock --check'
                        sh 'uv sync --locked'
                        sh 'uv run ruff format --check .'
                        sh 'uv run ruff check .'
                        sh 'uv run mypy'
                        sh 'uv run pytest'

                        // TODO: Alembic Revision Chain / Model Import 정적 검증 명령 확정 필요.
                        // 후보1) uv run pytest tests/persistence/test_migration_gate.py 로 이미 커버되는지 확인
                        // 후보2) uv run alembic check (또는 유사 정적 검증 서브커맨드) 존재 여부 확인
                        // 실제 DB에 연결하지 않는 정적 검증이어야 함 (PR 시점 DB Credential 사용 금지 원칙과 충돌 방지)
                    }
                }
            }
        }

        stage('Frontend: Lint & Test') {
            steps {
                dir('frontend') {
                    container('node') {
                        // node:24.19.0-alpine 이미지의 /usr/local/bin은 root 소유라
                        // runAsUser:1000에서 'corepack enable npm'이 심볼릭 링크를
                        // 바꿔치기하지 못해 EACCES가 발생함(실제 재현됨). 공식 지원되는
                        // --install-directory로 쓰기 가능한 경로에 shim을 만들고,
                        // 그 경로를 PATH 맨 앞에 추가해서 우회.
                        withEnv(['PATH+COREPACK=/tmp/corepack-bin']) {
                            sh 'mkdir -p /tmp/corepack-bin'
                            sh 'corepack enable --install-directory=/tmp/corepack-bin npm'
                            sh 'npm ci'
                            sh 'npm run typecheck'
                            sh 'npm test'
                            // 최신 Issue #40 확정 PR Gate: npm ci -> typecheck -> test -> build -> audit
                            // npm audit는 발견 즉시 기본적으로 nonzero exit로 실패 처리됨.
                            // TODO(팀 확인): --audit-level 임계치(예: high) 확정 필요, 현재는 기본값(모든 취약점) 적용.
                            sh 'npm audit'
                        }
                    }
                }
            }
        }

        stage('Backend: Build Verify (PR)') {
            steps {
                dir('backend') {
                    container('buildkit') {
                        withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                            sh '''
                                buildctl-daemonless.sh build \
                                  --frontend dockerfile.v0 \
                                  --local context=. \
                                  --local dockerfile=. \
                                  --output type=image,name=${REGISTRY_HOST}/${HARBOR_PROJECT}/backend:pr-verify,push=false
                            '''
                        }
                    }
                }
            }
        }

        stage('Frontend: Build Verify (PR)') {
            steps {
                dir('frontend') {
                    container('node') {
                        withEnv(['PATH+COREPACK=/tmp/corepack-bin']) {
                            sh 'mkdir -p /tmp/corepack-bin'
                            sh 'corepack enable --install-directory=/tmp/corepack-bin npm'
                            sh 'npm run build'
                        }
                    }
                    container('buildkit') {
                        withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                            sh '''
                                buildctl-daemonless.sh build \
                                  --frontend dockerfile.v0 \
                                  --local context=. \
                                  --local dockerfile=. \
                                  --output type=image,name=${REGISTRY_HOST}/${HARBOR_PROJECT}/frontend:pr-verify,push=false
                            '''
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            echo "Pipeline finished: branch=${env.BRANCH_NAME}, commit=${env.GIT_COMMIT_SHA}"
        }
    }
}
