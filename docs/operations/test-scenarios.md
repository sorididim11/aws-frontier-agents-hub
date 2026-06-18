# DevOps Agent 테스트 시나리오 스펙

## 개요

AWS DevOps Agent의 문제 진단 능력을 테스트하기 위한 시나리오 정의서.
각 시나리오는 실제 운영 환경에서 발생할 수 있는 문제를 시뮬레이션한다.

## 문제 카테고리 분류

### Layer 1: Application (앱)
| 카테고리 | 설명 | 예시 |
|---------|------|------|
| 버그 | 코드 에러, 예외 처리 실패 | NullPointer, 무한 루프 |
| 리소스 | 메모리/CPU/커넥션 관리 | 메모리 누수, 커넥션 풀 고갈 |
| 설정 | 앱 설정 오류 | 잘못된 타임아웃, 환경변수 |

### Layer 2: Kubernetes Platform (K8s)
| 카테고리 | 설명 | 예시 |
|---------|------|------|
| Pod | 컨테이너 실행 문제 | OOMKilled, CrashLoopBackOff |
| Deployment | 배포 관련 문제 | ImagePullBackOff, 롤아웃 실패 |
| Service | 서비스 네트워킹 | 서비스 디스커버리 실패, Endpoint 없음 |
| Config | 설정 리소스 | ConfigMap 누락, ResourceQuota 초과 |
| Node | 노드 상태 | NotReady, 리소스 pressure |
| Storage | 스토리지 | PVC 바인딩 실패, 마운트 에러 |
| Network | K8s 네트워크 | NetworkPolicy 차단, DNS 실패 |
| RBAC | 권한 | ServiceAccount 권한 부족 |
| HPA | 오토스케일링 | 스케일링 실패, metrics 없음 |

### Layer 3: AWS Infrastructure (AWS)
| 카테고리 | 설명 | 예시 |
|---------|------|------|
| EC2 | 인스턴스 | 노드 장애, 인스턴스 terminate |
| EKS | 클러스터 | API server 접근 불가, 애드온 장애 |
| RDS | 데이터베이스 | 연결 한계, 스토리지 부족, 파라미터 오류 |
| Security Group | 방화벽 | 인바운드/아웃바운드 차단 |
| IAM/IRSA | 권한 | 역할 권한 부족, AssumeRole 실패 |
| VPC/Network | 네트워크 | NAT Gateway 장애, 라우팅 오류 |
| Secrets Manager | 시크릿 | 접근 권한 없음, 시크릿 없음 |
| CloudWatch | 모니터링 | 에이전트 장애, 로그 수집 실패 |
| ALB/NLB | 로드밸런서 | 타겟 그룹 unhealthy |

### Layer 4: External Dependencies (외부)
| 카테고리 | 설명 | 예시 |
|---------|------|------|
| Third-party API | 외부 서비스 | API 장애, Rate limiting |
| DNS | 도메인 해석 | 외부 DNS 해석 실패 |
| Certificate | 인증서 | TLS 인증서 만료 |

### Layer 5: Deployment/Change (변경)
| 카테고리 | 설명 | 예시 |
|---------|------|------|
| 배포 | 배포 프로세스 | 이미지 태그 오류, 롤백 필요 |
| 설정 변경 | 런타임 설정 | 환경변수 변경 후 장애 |
| 스케일링 | 용량 변경 | HPA 오동작, 노드 스케일링 지연 |

---

## 구현된 시나리오

### Layer 1: Application

#### ✅ A01: OOMKilled (메모리 한계 초과)
| 항목 | 내용 |
|------|------|
| Layer | Application → Resource |
| 트리거 | `GET http://hasher/oom` 또는 `GET http://rng/oom` |
| 증상 | Pod 재시작, Last State: OOMKilled, Exit Code 137 |
| 원인 | 메모리 limit 128Mi 초과 |
| Agent 기대 진단 | Resource Limits - 메모리 limit 증가 권장 |

#### ✅ A02: High Latency (높은 지연시간)
| 항목 | 내용 |
|------|------|
| Layer | Application → Performance |
| 트리거 | `GET http://hasher/slow?delay=5` 또는 `GET http://rng/slow?delay=5` |
| 증상 | 응답 시간 증가, CloudWatch Latency 알람 |
| 원인 | 의도적 sleep 추가 |
| Agent 기대 진단 | Performance Issue - 병목 지점 식별 |

#### ✅ A03: HTTP 500 Errors (서버 에러)
| 항목 | 내용 |
|------|------|
| Layer | Application → Bug |
| 트리거 | `GET http://hasher/error` 또는 `GET http://rng/error` |
| 증상 | HTTP 500 응답, CloudWatch Error/Fault 알람 |
| 원인 | 의도적 에러 반환 |
| Agent 기대 진단 | Application Error - 에러 로그 분석 |

#### ✅ A04: CPU Spike (CPU 과부하)
| 항목 | 내용 |
|------|------|
| Layer | Application → Resource |
| 트리거 | `GET http://rng/cpu?duration=60` |
| 증상 | CPU 사용률 급증, 응답 지연 |
| 원인 | Busy loop으로 CPU 소모 |
| Agent 기대 진단 | Resource Issue - CPU throttling 확인 |

#### ✅ A05: Process Crash (프로세스 크래시)
| 항목 | 내용 |
|------|------|
| Layer | Application → Bug |
| 트리거 | `GET http://hasher/crash` 또는 `GET http://rng/crash` |
| 증상 | Pod 재시작, Exit Code 1 |
| 원인 | 프로세스 강제 종료 |
| Agent 기대 진단 | Application Error - 크래시 원인 분석 |

### Layer 2: Kubernetes Platform

#### ✅ K01: ImagePullBackOff (이미지 풀 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Deployment |
| 트리거 | `./trigger-scenarios.sh imagepull` |
| 증상 | Pod Pending, ImagePullBackOff 상태 |
| 원인 | 존재하지 않는 이미지 태그 |
| Agent 기대 진단 | Deployment Issue - 올바른 이미지 태그 확인 |

#### ✅ K02: CrashLoopBackOff (앱 시작 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Pod |
| 트리거 | `./trigger-scenarios.sh crashloop` |
| 증상 | Pod CrashLoopBackOff, 반복 재시작 |
| 원인 | 컨테이너 시작 시 즉시 exit 1 |
| Agent 기대 진단 | Application Error - 로그 확인, 설정 검증 |

#### ✅ K03: Service Discovery Failure (서비스 디스커버리 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Service |
| 트리거 | `./trigger-scenarios.sh servicediscovery` |
| 증상 | 연결 실패 로그, 서비스 호출 에러 |
| 원인 | 존재하지 않는 서비스 호출 |
| Agent 기대 진단 | Dependency Issue - 서비스 존재 확인 |

#### ✅ K04: Resource Quota Exceeded (리소스 쿼터 초과)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Config |
| 트리거 | `./trigger-scenarios.sh quota` |
| 증상 | Pod 생성 실패, Forbidden 이벤트 |
| 원인 | ResourceQuota 한계 초과 요청 |
| Agent 기대 진단 | Configuration Issue - 쿼터 조정 |

#### ✅ K05: ConfigMap Missing (설정 누락)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Config |
| 트리거 | `./trigger-scenarios.sh configmap` |
| 증상 | Pod CreateContainerConfigError |
| 원인 | 존재하지 않는 ConfigMap 참조 |
| Agent 기대 진단 | Configuration Issue - ConfigMap 생성 필요 |

#### ✅ K06: PVC Binding Failure (스토리지 바인딩 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Storage |
| 트리거 | `./trigger-scenarios.sh pvc` |
| 증상 | PVC Pending, Pod 시작 불가 |
| 원인 | 존재하지 않는 StorageClass |
| Agent 기대 진단 | Storage Issue - StorageClass 확인 |

#### ✅ K07: NetworkPolicy Block (네트워크 정책 차단)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Network |
| 트리거 | `./trigger-scenarios.sh networkpolicy` |
| 증상 | Pod 간 통신 실패, 타임아웃 |
| 원인 | NetworkPolicy로 트래픽 차단 |
| Agent 기대 진단 | Network Issue - NetworkPolicy 확인 |

#### ✅ K08: HPA Scaling Failure (오토스케일링 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → HPA |
| 트리거 | `./trigger-scenarios.sh hpa` |
| 증상 | HPA unable to fetch metrics |
| 원인 | 존재하지 않는 커스텀 메트릭 |
| Agent 기대 진단 | Scaling Issue - metrics 설정 확인 |

#### ✅ K09: Secret Missing (시크릿 누락)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Config |
| 트리거 | `./trigger-scenarios.sh secret` |
| 증상 | Pod CreateContainerConfigError |
| 원인 | 존재하지 않는 Secret 참조 |
| Agent 기대 진단 | Configuration Issue - Secret 생성 필요 |

#### ✅ K10: Liveness Probe Failure (활성 프로브 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Pod |
| 트리거 | `./trigger-scenarios.sh liveness` |
| 증상 | Pod 반복 재시작 |
| 원인 | Liveness probe 실패 |
| Agent 기대 진단 | Health Check Issue - probe 설정 확인 |

#### ✅ K11: Readiness Probe Failure (준비 프로브 실패)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Pod |
| 트리거 | `./trigger-scenarios.sh readiness` |
| 증상 | Pod Ready 0/1, Endpoints 없음 |
| 원인 | Readiness probe 실패 |
| Agent 기대 진단 | Health Check Issue - probe 설정 확인 |

#### 🔲 K12: Node NotReady (노드 장애)
| 항목 | 내용 |
|------|------|
| Layer | K8s → Node |
| 트리거 | `kubectl cordon <node>` + `kubectl drain <node>` |
| 증상 | Pod Pending, 노드 NotReady |
| 원인 | 노드 사용 불가 |
| Agent 기대 진단 | Infrastructure Issue - 노드 상태 확인 |

### Layer 3: AWS Infrastructure

#### ✅ I01: RDS Connection Leak/Flood (DB 연결 누수/폭주)
| 항목 | 내용 |
|------|------|
| Layer | AWS → RDS |
| 트리거 | `31, db-leak` (연결 누수), `32, db-flood` (연결 폭주) |
| 증상 | DB 연결 실패, "too many connections" |
| 원인 | 연결 풀 미사용으로 인한 연결 누수 |
| Agent 기대 진단 | Database Issue - 연결 풀 사용 권장 |
| 비교 시나리오 | `30, db-pool` (정상 연결 풀 사용) |

#### ✅ I02: Security Group Block (보안 그룹 차단)
| 항목 | 내용 |
|------|------|
| Layer | AWS → Security Group |
| 트리거 | `20, sg-block` (복원: `21, sg-restore`) |
| 증상 | 연결 타임아웃, 서비스 접근 불가 |
| 원인 | Security Group 규칙 누락 |
| Agent 기대 진단 | Network Issue - Security Group 규칙 확인 |

#### ✅ I03: IAM Permission Denied (IAM 권한 거부)
| 항목 | 내용 |
|------|------|
| Layer | AWS → IAM/IRSA |
| 트리거 | `25, iam-deny` |
| 증상 | AccessDenied 에러, AWS API 호출 실패 |
| 원인 | IAM 권한 부족 |
| Agent 기대 진단 | Permission Issue - IAM 정책 확인 |

#### ✅ I04: Secrets Manager Access Failure (시크릿 접근 실패)
| 항목 | 내용 |
|------|------|
| Layer | AWS → Secrets Manager |
| 트리거 | `26, secrets-missing` |
| 증상 | 앱 시작 실패, 시크릿 로드 에러 |
| 원인 | 시크릿 없음 또는 권한 없음 |
| Agent 기대 진단 | Configuration Issue - 시크릿 존재/권한 확인 |

#### ✅ I05: External Connectivity Test (외부 연결 테스트)
| 항목 | 내용 |
|------|------|
| Layer | AWS → VPC/Network |
| 트리거 | `27, external-connectivity` |
| 증상 | 외부 API 호출 실패, 이미지 풀 실패 |
| 원인 | NAT Gateway 또는 라우팅 문제 |
| Agent 기대 진단 | Network Issue - NAT Gateway/라우팅 확인 |

### Layer 3-FIS: AWS FIS Chaos Engineering

#### ✅ I07: EKS Node Termination (노드 종료)
| 항목 | 내용 |
|------|------|
| Layer | AWS → EC2/EKS |
| 트리거 | `41, fis-node-terminate` |
| 증상 | Pod 재스케줄링, 일시적 서비스 중단 |
| 원인 | FIS가 EKS 노드 인스턴스 종료 |
| Agent 기대 진단 | Infrastructure Issue - 노드 장애 감지, Pod 재배치 확인 |

#### ✅ I08: RDS Reboot (DB 재시작)
| 항목 | 내용 |
|------|------|
| Layer | AWS → RDS |
| 트리거 | `42, fis-rds-reboot` |
| 증상 | DB 연결 일시 중단, 앱 에러 |
| 원인 | FIS가 RDS 인스턴스 재시작 |
| Agent 기대 진단 | Database Issue - RDS 재시작 감지, 연결 재시도 권장 |

#### ✅ I09: RDS Multi-AZ Failover (DB 페일오버)
| 항목 | 내용 |
|------|------|
| Layer | AWS → RDS |
| 트리거 | `43, fis-rds-failover` |
| 증상 | DB 연결 일시 중단, 엔드포인트 변경 |
| 원인 | FIS가 Multi-AZ 페일오버 강제 실행 |
| Agent 기대 진단 | Database Issue - 페일오버 감지, DNS 캐시 확인 |

#### ✅ I10: Network Disruption (네트워크 장애)
| 항목 | 내용 |
|------|------|
| Layer | AWS → VPC |
| 트리거 | `44, fis-network-disrupt` |
| 증상 | 서브넷 간 통신 실패, 타임아웃 |
| 원인 | FIS가 서브넷 연결 차단 |
| Agent 기대 진단 | Network Issue - 네트워크 연결 장애 감지 |

#### ✅ I11: Node CPU Stress (노드 CPU 스트레스)
| 항목 | 내용 |
|------|------|
| Layer | AWS → EC2 |
| 트리거 | `45, fis-cpu-stress` |
| 증상 | 노드 CPU 100%, Pod 성능 저하 |
| 원인 | FIS가 SSM으로 CPU 스트레스 주입 |
| Agent 기대 진단 | Resource Issue - 노드 CPU 과부하 감지 |

#### ✅ I12: Node Memory Stress (노드 메모리 스트레스)
| 항목 | 내용 |
|------|------|
| Layer | AWS → EC2 |
| 트리거 | `46, fis-memory-stress` |
| 증상 | 노드 메모리 80%+, OOM 위험 |
| 원인 | FIS가 SSM으로 메모리 스트레스 주입 |
| Agent 기대 진단 | Resource Issue - 노드 메모리 압박 감지 |

### Layer 4: External Dependencies

#### ✅ E01: Dependency Failure (의존성 실패)
| 항목 | 내용 |
|------|------|
| Layer | External → Third-party |
| 트리거 | `GET http://rng/dependency-fail` |
| 증상 | HTTP 503, 타임아웃 에러 |
| 원인 | 외부 서비스 연결 실패 시뮬레이션 |
| Agent 기대 진단 | Dependency Issue - 다운스트림 서비스 상태 확인 |

---

## 시나리오 요약

| ID | 시나리오 | Layer | 상태 | 트리거 |
|----|---------|-------|------|--------|
| A01 | OOMKilled | Application | ✅ | `1, oom` |
| A02 | High Latency | Application | ✅ | `4, latency` |
| A03 | HTTP 500 | Application | ✅ | `7, error` |
| A04 | CPU Spike | Application | ✅ | `8, cpu` |
| A05 | Process Crash | Application | ✅ | `/crash` |
| K01 | ImagePullBackOff | K8s | ✅ | `2, imagepull` |
| K02 | CrashLoopBackOff | K8s | ✅ | `3, crashloop` |
| K03 | Service Discovery | K8s | ✅ | `6, servicediscovery` |
| K04 | ResourceQuota | K8s | ✅ | `5, quota` |
| K05 | ConfigMap Missing | K8s | ✅ | `13, configmap` |
| K06 | PVC Binding | K8s | ✅ | `11, pvc` |
| K07 | NetworkPolicy | K8s | ✅ | `10, networkpolicy` |
| K08 | HPA Failure | K8s | ✅ | `12, hpa` |
| K09 | Secret Missing | K8s | ✅ | `14, secret` |
| K10 | Liveness Probe | K8s | ✅ | `15, liveness` |
| K11 | Readiness Probe | K8s | ✅ | `16, readiness` |
| K12 | Node NotReady | K8s | 🔲 | manual |
| I01 | RDS Connection Leak/Flood | AWS | ✅ | `30-34, db-*` |
| I02 | Security Group Block | AWS | ✅ | `20, sg-block` |
| I03 | IAM Permission Denied | AWS | ✅ | `25, iam-deny` |
| I04 | Secrets Manager Access | AWS | ✅ | `26, secrets-missing` |
| I05 | External Connectivity | AWS | ✅ | `27, external-connectivity` |
| I07 | EKS Node Terminate | AWS-FIS | ✅ | `41, fis-node-terminate` |
| I08 | RDS Reboot | AWS-FIS | ✅ | `42, fis-rds-reboot` |
| I09 | RDS Failover | AWS-FIS | ✅ | `43, fis-rds-failover` |
| I10 | Network Disruption | AWS-FIS | ✅ | `44, fis-network-disrupt` |
| I11 | Node CPU Stress | AWS-FIS | ✅ | `45, fis-cpu-stress` |
| I12 | Node Memory Stress | AWS-FIS | ✅ | `46, fis-memory-stress` |
| E01 | Dependency Fail | External | ✅ | `9, dependency` |
| C01 | Redis 장애 전파 | Composite | ✅ | `50, composite-redis-cascade` |
| C02 | 네트워크 차단 연쇄 | Composite | ✅ | `51, composite-network-cascade` |
| C03 | 노드 장애 연쇄 | Composite | ✅ | `52, composite-node-cascade` |
| C04 | RDS 페일오버 연쇄 | Composite | ✅ | `53, composite-rds-cascade` |
| C05 | 서비스 의존성 장애 | Composite | ✅ | `54, composite-service-cascade` |
| C06 | 리소스 경쟁 연쇄 | Composite | ✅ | `55, composite-resource-cascade` |
| C07 | 데이터 오염 연쇄 | Composite | ✅ | `60, corrupted-data` |

---

## 트리거 스크립트 사용법

```bash
# 스크립트 위치
infrastructure/trigger-scenarios.sh

# 사용법
./trigger-scenarios.sh <scenario>

# Application Layer
1, oom              - OOMKilled
4, latency          - High Latency
7, error            - HTTP 500 Errors
8, cpu              - CPU Spike

# Kubernetes Layer
2, imagepull        - ImagePullBackOff
3, crashloop        - CrashLoopBackOff
5, quota            - Resource Quota Exceeded
6, servicediscovery - Service Discovery Failure
10, networkpolicy   - NetworkPolicy Block
11, pvc             - PVC Binding Failure
12, hpa             - HPA Scaling Failure
13, configmap       - ConfigMap Missing
14, secret          - Secret Missing
15, liveness        - Liveness Probe Failure
16, readiness       - Readiness Probe Failure

# Database Connection
30, db-pool         - DB Connection Pool (정상)
31, db-leak         - DB Connection Leak
32, db-flood        - DB Connection Flood
33, db-leak-status  - Leak 상태 확인
34, db-leak-cleanup - Leak 정리

# AWS FIS Experiments
40, fis-list        - FIS 템플릿 목록
41, fis-node-terminate - EKS 노드 종료
42, fis-rds-reboot  - RDS 재시작
43, fis-rds-failover - RDS Multi-AZ 페일오버
44, fis-network-disrupt - 네트워크 장애
45, fis-cpu-stress  - 노드 CPU 스트레스
46, fis-memory-stress - 노드 메모리 스트레스
47, fis-status      - 실험 상태 확인
48, fis-stop <id>   - 실험 중지

# External
9, dependency       - Dependency Failure

# Utility
cleanup             - 테스트 리소스 정리
status              - 현재 상태 확인
```

---

## 구현 파일 목록

| 파일 | 설명 |
|------|------|
| `services/dockercoins/hasher/hasher.rb` | hasher 서비스 시뮬레이션 엔드포인트 |
| `services/dockercoins/rng/rng.py` | rng 서비스 시뮬레이션 엔드포인트 (DB 연결 포함) |
| `infrastructure/kubernetes/dockercoins/test-scenarios.yaml` | K8s 테스트 리소스 정의 |
| `infrastructure/trigger-scenarios.sh` | 시나리오 트리거 스크립트 |
| `infrastructure/cloudformation/06-cloudwatch-alarms.yml` | CloudWatch 알람 정의 |
| `infrastructure/cloudformation/07-fis-experiments.yml` | AWS FIS 실험 템플릿 |

---

## DevOps Agent 지원 Root Cause 카테고리

DevOps Agent가 식별할 수 있는 근본 원인 카테고리:

1. **System Changes** - 최근 배포/설정 변경
2. **Input Anomalies** - 비정상적인 입력/트래픽
3. **Resource Limits** - 리소스 한계 도달
4. **Component Failures** - 컴포넌트 장애
5. **Dependency Issues** - 의존성 문제

---

## 테스트 절차

1. 시나리오 트리거 실행
2. 증상 확인 (kubectl, CloudWatch)
3. DevOps Agent Investigation 시작 (수동)
4. Agent 진단 결과 검증
5. 테스트 리소스 정리 (`./trigger-scenarios.sh cleanup`)

---

## Layer 6: Composite Scenarios (복합 시나리오)

실제 운영 환경에서 발생하는 **인과관계가 있는 연쇄 장애**를 시뮬레이션.
하나의 근본 원인이 여러 레이어에 걸쳐 영향을 미치는 현실적인 케이스.

### 복합 시나리오 설계 원칙
1. **인과관계 명확**: A → B → C 형태의 연쇄 장애
2. **현실성**: 실제 운영에서 발생 가능한 패턴
3. **검증 가능**: 각 단계의 영향을 확인 가능

---

### C01: Redis 장애 전파 (Cache/Queue Cascade)
| 항목 | 내용 |
|------|------|
| Layers | Infrastructure → Application |
| 근본 원인 | Redis Pod 다운 |
| 연쇄 흐름 | `redis 다운` → `worker ConnectionError` → `처리량 0` → `webui 표시 중단` |
| 트리거 | `50, composite-redis-cascade` |
| 증상 | worker 에러 로그, 처리량 0 |
| Agent 기대 | redis가 근본 원인임을 식별 (worker가 아닌) |
| 현실성 | ✅ 캐시/큐 서버 장애는 매우 흔함 |

**검증된 인과관계:**
```
redis Pod 삭제
  ↓ (즉시)
worker: "redis.exceptions.ConnectionError: Error 111 connecting to redis:6379"
  ↓ (즉시)
worker: "Waiting 10s and restarting"
  ↓ (연속)
전체 처리량 0
```

---

### C02: 네트워크 차단 연쇄 (Network → DB → App Cascade)
| 항목 | 내용 |
|------|------|
| Layers | AWS Security Group → RDS → Application |
| 근본 원인 | Security Group에서 RDS 포트 차단 |
| 연쇄 흐름 | `SG 규칙 제거` → `DB 연결 타임아웃` → `rng HTTP 에러` → `worker 영향` |
| 트리거 | `51, composite-network-cascade` |
| 증상 | Connection timeout, HTTP 500 |
| Agent 기대 | SG 변경이 근본 원인임을 식별 |
| 현실성 | ✅ SG 설정 실수는 흔한 운영 실수 |

**검증된 인과관계:**
```
SG에서 5432 포트 차단
  ↓ (즉시)
rng: DB 연결 타임아웃 (30초)
  ↓ (타임아웃 후)
rng: "ERROR: Database query failed"
  ↓ (즉시)
DB 의존 기능 사용 불가
```

---

### C03: 노드 장애 연쇄 (Node → Pod → Service Cascade)
| 항목 | 내용 |
|------|------|
| Layers | AWS EC2 → Kubernetes → Application |
| 근본 원인 | EKS 노드 종료 (FIS) |
| 연쇄 흐름 | `노드 종료` → `Pod Terminating` → `재스케줄링` → `일시적 서비스 중단` |
| 트리거 | `52, composite-node-cascade` |
| 증상 | Pod Pending/Terminating, 서비스 불안정 |
| Agent 기대 | 노드 장애가 근본 원인임을 식별 |
| 현실성 | ✅ 노드 장애는 클라우드에서 흔함 (spot 인스턴스, 하드웨어 장애) |

**예상 인과관계:**
```
FIS로 노드 1개 종료
  ↓ (즉시)
해당 노드의 Pod들 Terminating
  ↓ (30초~1분)
Pod들 다른 노드에 재스케줄링
  ↓ (재스케줄링 중)
서비스 일시적 불안정
```

---

### C04: RDS 페일오버 연쇄 (DB Failover → Connection → App Cascade)
| 항목 | 내용 |
|------|------|
| Layers | AWS RDS → Connection Pool → Application |
| 근본 원인 | RDS 재시작/페일오버 |
| 연쇄 흐름 | `RDS 재시작` → `기존 연결 끊김` → `앱 에러` → `복구` |
| 트리거 | `53, composite-rds-cascade` |
| 증상 | DB 연결 에러, 일시적 서비스 중단 |
| Agent 기대 | RDS 이벤트와 앱 에러 연관성 식별 |
| 현실성 | ✅ RDS 유지보수/페일오버는 실제로 발생 |

**검증된 인과관계:**
```
FIS로 RDS 재시작
  ↓ (즉시)
rng: "SSL connection has been closed unexpectedly"
  ↓ (RDS 재시작 중)
DB 연결 시도 실패
  ↓ (RDS 복구 후)
정상화 (Pod 재시작 필요할 수 있음)
```

---

### C05: 서비스 의존성 장애 (Service Dependency Cascade)
| 항목 | 내용 |
|------|------|
| Layers | Application → Application |
| 근본 원인 | hasher 서비스 다운 |
| 연쇄 흐름 | `hasher 다운` → `worker 호출 실패` → `처리량 저하` |
| 트리거 | `54, composite-service-cascade` |
| 증상 | worker ConnectionError, 처리량 저하 |
| Agent 기대 | hasher가 근본 원인임을 식별 (worker가 아닌) |
| 현실성 | ✅ 마이크로서비스 의존성 장애는 매우 흔함 |

**검증된 인과관계:**
```
hasher Pod 삭제
  ↓ (즉시)
worker: "requests.exceptions.ConnectionError: HTTPConnectionPool(host='hasher')"
  ↓ (즉시)
worker: "Waiting 10s and restarting"
  ↓ (연속)
처리량 저하
```

---

### C06: 리소스 경쟁 연쇄 (Resource Contention Cascade)
| 항목 | 내용 |
|------|------|
| Layers | AWS EC2 → Kubernetes → Application |
| 근본 원인 | 노드 CPU 리소스 부족 |
| 연쇄 흐름 | `CPU 스트레스` → `앱 응답 지연` → `타임아웃` → `에러 증가` |
| 트리거 | `55, composite-resource-cascade` |
| 증상 | High CPU, Latency 증가, 타임아웃 |
| Agent 기대 | CPU 리소스 부족이 근본 원인임을 식별 |
| 현실성 | ✅ 리소스 경쟁은 멀티테넌트 환경에서 흔함 |

**예상 인과관계:**
```
FIS로 노드 CPU 스트레스 (80%)
  ↓ (즉시)
모든 Pod CPU throttling
  ↓ (즉시)
응답 시간 증가
  ↓ (타임아웃 설정에 따라)
클라이언트 타임아웃/에러
```

---

### C07: 데이터 오염 연쇄 (Data Corruption Cascade)
| 항목 | 내용 |
|------|------|
| Layers | Application → Application |
| 근본 원인 | RNG 데이터 오염 (환경변수 설정) |
| 연쇄 흐름 | `RNG 오염 데이터 반환` → `worker 전달` → `hasher 검증 실패` → `HTTP 400 + 에러 로그` |
| 트리거 | `60, corrupted-data` |
| 증상 | hasher HTTP 400 에러, 에러 로그, CloudWatch 알람 |
| Agent 기대 | RNG 데이터 오염이 근본 원인임을 식별 (hasher 버그가 아닌) |
| 현실성 | ✅ 업스트림 서비스 데이터 품질 문제는 매우 흔함 |

**검증된 인과관계:**
```
RNG_CORRUPTION_RATE=0.5 설정
  ↓ (즉시)
RNG가 50% 확률로 빈 데이터 반환
  ↓ (worker 호출 시)
worker가 빈 데이터를 hasher로 전달
  ↓ (즉시)
hasher 입력 검증 실패
  ↓ (즉시)
HTTP 400 + ERROR 로그: "Empty input received from client"
  ↓ (누적)
CloudWatch 알람 ALARM 상태
```

**구현 세부사항:**
- RNG: `RNG_CORRUPTION_RATE` 환경변수로 오염 확률 제어 (0.0~1.0)
- Hasher: 입력 검증 (0 bytes → 400, <16 bytes → 400)
- Hasher: 에러 로깅 (`app.logger.error()`) - DevOps Agent가 원인 추적 가능
- CloudWatch 알람: `hasher-errors` (threshold: 5)
- X-Ray: 전체 서비스 체인 trace (worker → rng → worker → hasher)

**복원:**
- `61, corrupted-data-restore` - RNG 정상 모드로 복원
- `62, corrupted-data-status` - 현재 오염 상태 확인

---

## 복합 시나리오 요약

| ID | 시나리오 | 근본 원인 | 영향 범위 | 난이도 | 트리거 |
|----|---------|----------|----------|--------|--------|
| C01 | Redis 장애 전파 | redis 다운 | worker → webui | ⭐⭐ | `50` |
| C02 | 네트워크 차단 연쇄 | SG 규칙 제거 | DB → rng | ⭐⭐ | `51` |
| C03 | 노드 장애 연쇄 | 노드 종료 | 다중 Pod | ⭐⭐⭐ | `52` |
| C04 | RDS 페일오버 연쇄 | RDS 재시작 | rng → worker | ⭐⭐⭐ | `53` |
| C05 | 서비스 의존성 장애 | hasher 다운 | worker | ⭐⭐ | `54` |
| C06 | 리소스 경쟁 연쇄 | CPU 부족 | 전체 서비스 | ⭐⭐⭐ | `55` |
