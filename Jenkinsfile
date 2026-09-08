// 작성자: 최유준
// 작성 날짜: 2026-09-07
//
// seokpan-app 표준 Jenkinsfile (Issue #40)
//
// 범위(확정, 09-04 세션 인수인계 기준):
//   - PR Pipeline: Backend/Frontend Test + Build 검증까지만 수행. Harbor Push 금지,
//     GitOps 변경 금지, 실제 DB/Redis Credential 사용 금지.
//   - main Merge 이후: main 커밋 기준으로 이미지 재빌드 -> SBOM/Provenance/Scan ->
//     Harbor Push -> Tag/Digest 확인. PR 시점에 만든 이미지는 재사용하지 않음.
//   - GitOps 연결(Branch/Commit/Push/PR 자동 생성)은 이번 Issue 범위 밖 -> 절대 포함하지 않음.
//
// 환경 제약(확정):
//   - Agent Label: buildkit-rootless
//   - DOCKER_CONFIG: /home/user/.docker (harbor-robot-dockerconfig Secret, Opaque 타입, key: config.json)
//   - Harbor CA: /etc/buildkit/certs/ca.crt (SSL_CERT_FILE 경유로 buildctl-daemonless.sh에 신뢰)
//   - buildkit 컨테이너 내 사용 가능 도구: git, wget(BusyBox), sed 만 존재.
//     curl / kustomize / jq 없음, non-root라 apk install도 불가.
//
// 미확정/미검증 항목은 하단 TODO 및 대화 내 질문 참고.
//
// [선행 조건] 이 Jenkinsfile은 buildkit-rootless PodTemplate에 'python', 'node' 컨테이너가
// 추가되어 있어야 동작합니다(container('python'), container('node') 참조). 아직 추가 전이면
// seokpan-gitops의 cicd/jenkins-jcasc-configmap.yaml에 먼저 반영 필요 - 별도 Issue/PR 권장.

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
        // PR Pipeline: Test + Build 검증까지만. main 브랜치에서는 skip.
        // ------------------------------------------------------------------

        stage('Backend: Lint & Test') {
            when { not { branch 'main' } }
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
            when { not { branch 'main' } }
            steps {
                dir('frontend') {
                    container('node') {
                        sh 'corepack enable npm'
                        sh 'npm ci'
                        sh 'npm run typecheck'
                        sh 'npm test'
                    }
                }
            }
        }

        stage('Backend: Build Verify (PR)') {
            when { not { branch 'main' } }
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
            when { not { branch 'main' } }
            steps {
                dir('frontend') {
                    container('node') {
                        sh 'corepack enable npm'
                        sh 'npm run build'
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

        // ------------------------------------------------------------------
        // main Merge 이후: 별도 Stage/조건으로 완전히 분리된 Image 흐름.
        // PR 시점 이미지를 재사용하지 않고 main 커밋 기준으로 다시 빌드.
        // ------------------------------------------------------------------

        // ------------------------------------------------------------------
        // [팀 확인 필요 - 설계 결정] Scan-before-Push 순서 문제
        //
        // 문서 확정 스펙은 "Build -> Scan(중단 조건 포함) -> Push -> Digest 확인" 순서지만,
        // buildkit-rootless는 데몬 없이 호출마다 ephemeral하게 뜨기 때문에 "로컬 빌드 결과물을
        // 들고 있다가 스캔 통과 후 Push"가 단순하지 않음(도커 데몬도, 로컬 이미지 스토리지도
        // 없음). 아래는 그 문제를 우회하기 위해 채택한 방식이며, 정식 채택 전 팀 검토 필요:
        //
        //   1) 후보 태그(<tag>-scanning)로 Build & Push
        //   2) 후보 태그를 대상으로 원격 스캔 수행 (trivy/grype 등은 로컬 이미지가 아니어도
        //      레지스트리 이미지를 직접 스캔 가능)
        //   3) 통과 시 Harbor "태그 추가" API로 최종 태그(git-<sha>)를 같은 아티팩트에 추가
        //      (재빌드/재푸시 없음 -> 최종 이미지 Digest는 후보 태그와 완전히 동일함이 보장됨)
        //   4) 후보 태그 삭제는 생략함 (BusyBox wget이 DELETE 메서드를 지원하지 않아 buildkit
        //      컨테이너 안에서 확실하게 삭제할 방법이 없음) -> Harbor Tag Retention 정책으로
        //      "*-scanning" 패턴을 주기적으로 자동 정리하는 방안을 팀에 제안 예정
        // ------------------------------------------------------------------

        stage('Resolve Image Tag (main)') {
            when { branch 'main' }
            steps {
                script {
                    env.FINAL_TAG = "git-${env.GIT_COMMIT_SHA.take(12)}"
                    env.CANDIDATE_TAG = "${env.FINAL_TAG}-scanning"
                    echo "FINAL_TAG=${env.FINAL_TAG}, CANDIDATE_TAG=${env.CANDIDATE_TAG}"
                }
            }
        }

        stage('Guard: Final Tag Not Already Pushed (main)') {
            // "동일 Tag 재Push 금지" 원칙 -> 최종 태그 기준으로 사전 확인.
            // ~/.docker/config.json의 "auth"(base64 user:pass)를 그대로 재사용, jq 없이 grep/sed 파싱.
            //
            // 확인 필요(Harbor 2.15.2): Robot Account가 Basic Auth로 이 Project API에 접근
            // 가능한지. Harbor 2.10 이전 버전은 Robot Account의 REST API(/api/v2.0/...) 접근이
            // 401/403으로 막혀있던 이슈가 있었음 -> 2.15.2는 해결된 것으로 보이나 팀에서 실제
            // 응답으로 재확인 부탁. 아직 Push 전이므로 조회 결과 404(Not Found)가 정상 케이스.
            when { branch 'main' }
            steps {
                container('buildkit') {
                    withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                        script {
                            ['backend', 'frontend'].each { svc ->
                                sh """
                                    AUTH_B64=\$(grep -A2 "\${REGISTRY_HOST}" \${DOCKER_CONFIG}/config.json \\
                                      | grep '"auth"' \\
                                      | sed -E 's/.*"auth"[[:space:]]*:[[:space:]]*"([^"]+)".*/\\1/')

                                    HTTP_STATUS=\$(wget -q -O /tmp/${svc}-final.json --server-response \\
                                      --header="Authorization: Basic \${AUTH_B64}" \\
                                      "https://\${REGISTRY_HOST}/api/v2.0/projects/\${HARBOR_PROJECT}/repositories/${svc}/artifacts/\${FINAL_TAG}" \\
                                      2>&1 | awk '/^  HTTP/{print \$2}' | tail -1)

                                    echo "${svc} final tag 조회 HTTP status: \${HTTP_STATUS}"
                                    if [ "\${HTTP_STATUS}" = "200" ]; then
                                      echo "이미 존재하는 Tag(\${FINAL_TAG})입니다. 동일 Tag 재Push는 금지되어 있습니다."
                                      exit 1
                                    fi
                                """
                            }
                        }
                    }
                }
            }
        }

        stage('Diagnose: Image Tooling (main)') {
            // 1회성 진단 Stage. buildkit-rootless 컨테이너에 SBOM/Scan 도구가 실제로
            // 있는지 확인하기 위한 용도. 확인이 끝나면 이 Stage는 제거하고
            // 아래 'SBOM / Provenance / Scan' Stage에 실제 명령을 채워 넣을 것.
            when { branch 'main' }
            steps {
                container('buildkit') {
                    sh '''
                        echo "--- which 결과 ---"
                        which syft   || echo "syft not found"
                        which trivy  || echo "trivy not found"
                        which grype  || echo "grype not found"
                        which cosign || echo "cosign not found"
                        echo "--- 버전(있는 경우) ---"
                        syft version   2>&1 || true
                        trivy --version 2>&1 || true
                        grype version  2>&1 || true
                        cosign version 2>&1 || true
                    '''
                }
            }
        }

        stage('Backend: Build & Push Candidate (main)') {
            when { branch 'main' }
            steps {
                dir('backend') {
                    container('buildkit') {
                        withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                            sh '''
                                buildctl-daemonless.sh build \
                                  --frontend dockerfile.v0 \
                                  --local context=. \
                                  --local dockerfile=. \
                                  --output type=image,name=${REGISTRY_HOST}/${HARBOR_PROJECT}/backend:${CANDIDATE_TAG},push=true
                            '''
                        }
                    }
                }
            }
        }

        stage('Frontend: Build & Push Candidate (main)') {
            when { branch 'main' }
            steps {
                dir('frontend') {
                    container('buildkit') {
                        withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                            sh '''
                                buildctl-daemonless.sh build \
                                  --frontend dockerfile.v0 \
                                  --local context=. \
                                  --local dockerfile=. \
                                  --output type=image,name=${REGISTRY_HOST}/${HARBOR_PROJECT}/frontend:${CANDIDATE_TAG},push=true
                            '''
                        }
                    }
                }
            }
        }

        stage('SBOM / Provenance / Scan (main)') {
            when { branch 'main' }
            steps {
                // TODO: 'Diagnose: Image Tooling' Stage 로그 확인 후 실제 명령으로 교체.
                // - 도구가 있는 경우: 예) trivy image --exit-code 1 --severity CRITICAL,HIGH
                //   --ignore-unfixed ${REGISTRY_HOST}/${HARBOR_PROJECT}/backend:${CANDIDATE_TAG}
                //   ("수정 가능한" HIGH만 걸러야 하므로 --ignore-unfixed 필요할 가능성 높음)
                // - 도구가 없는 경우: JCasC의 buildkit-rootless PodTemplate에 scanner 사이드카
                //   컨테이너 추가 필요 (Jenkinsfile에서는 container('scanner') { sh '...' } 로 호출)
                //
                // 정책(확정): CRITICAL 및 수정 가능한 HIGH 발견 시 파이프라인 중단(exit 1 등).
                echo 'TODO: SBOM/Provenance/Scan 실제 명령으로 교체 필요 (backend, frontend 각각)'
                error('SBOM/Provenance/Scan 단계 미구현 - Diagnose Stage 로그 확인 후 구현 예정')
            }
        }

        stage('Promote: Add Final Tag (main)') {
            // Scan 통과 후에만 도달. 재빌드/재푸시 없이 Harbor "태그 추가" API로 후보 태그와
            // 동일한 아티팩트에 최종 태그를 추가 -> 최종 이미지 Digest = 후보 태그 Digest.
            when { branch 'main' }
            steps {
                container('buildkit') {
                    withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                        script {
                            ['backend', 'frontend'].each { svc ->
                                sh """
                                    AUTH_B64=\$(grep -A2 "\${REGISTRY_HOST}" \${DOCKER_CONFIG}/config.json \\
                                      | grep '"auth"' \\
                                      | sed -E 's/.*"auth"[[:space:]]*:[[:space:]]*"([^"]+)".*/\\1/')

                                    wget -q -O - --method=POST \\
                                      --header="Authorization: Basic \${AUTH_B64}" \\
                                      --header="Content-Type: application/json" \\
                                      --body-data="{\\"name\\":\\"\${FINAL_TAG}\\"}" \\
                                      "https://\${REGISTRY_HOST}/api/v2.0/projects/\${HARBOR_PROJECT}/repositories/${svc}/artifacts/\${CANDIDATE_TAG}/tags"
                                """
                            }
                        }
                    }
                }
                // TODO: BusyBox wget이 --method=POST/--body-data 조합을 지원하는지 실제 확인
                // 필요. 미지원 시 GNU wget이 있는 별도 sidecar 사용, 혹은 buildctl-daemonless.sh와
                // 함께 배포되는 wget 버전 자체를 먼저 `wget --version`으로 확인 요망.
            }
        }

        stage('Verify Push (main)') {
            // 최종 태그의 Digest를 재조회해서 확인.
            when { branch 'main' }
            steps {
                container('buildkit') {
                    withEnv(['SSL_CERT_FILE=/etc/buildkit/certs/ca.crt']) {
                        script {
                            ['backend', 'frontend'].each { svc ->
                                sh """
                                    AUTH_B64=\$(grep -A2 "\${REGISTRY_HOST}" \${DOCKER_CONFIG}/config.json \\
                                      | grep '"auth"' \\
                                      | sed -E 's/.*"auth"[[:space:]]*:[[:space:]]*"([^"]+)".*/\\1/')

                                    wget -q -O - \\
                                      --header="Authorization: Basic \${AUTH_B64}" \\
                                      "https://\${REGISTRY_HOST}/api/v2.0/projects/\${HARBOR_PROJECT}/repositories/${svc}/artifacts/\${FINAL_TAG}" \\
                                      | grep -o '"digest"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1
                                """
                            }
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
