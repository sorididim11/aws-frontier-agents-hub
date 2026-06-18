"""Platform-agnostic failure mode definitions for chaos scenario generation.

16 failure modes that work on ANY AWS compute platform (EKS, ECS, EC2, Lambda)
without requiring app code access or modification. Triggers use only AWS API / FIS.

FM-01~FM-10: Infrastructure & basic application (network, compute, dependency, etc.)
FM-12,15,18,19,21,22: Business logic & general application (data, cache, scaling, observability, storage)

## Template Schema (표준 포맷)

각 failure mode template은 다음 필드를 포함해야 합니다:

```
{
  "id": "FM-XX",                    # 고유 식별자
  "name": "Human-readable name",    # 영문 짧은 이름
  "layer": "infrastructure|application|data|observability",  # 장애 레이어
  "description": "...",             # 장애 설명 (무엇을, 왜)
  "trigger_mode": "reactive|proactive|either",  # 조사 트리거 방식
  "trigger_mechanism": [...],       # 장애 주입 방법 (AWS API/FIS 명령)
  "observation_signals": {          # ★ 관측 신호 정의 (effect_type 분류)
    "trigger_active": [             # Phase 1: 장애 주입 성공 확인 신호
      {"signal": "name", "effect_type": "infra_state", "description": "..."},
    ],
    "effect_observed": [            # Phase 2: 장애 효과 관측 신호
      {
        "signal": "name",
        "effect_type": "infra_state|metric_observed|app_dependent",
        "confidence": "high|medium|low",
        "description": "무엇이 관측되는지",
        "verification_hint": "구체적 확인 방법 (kubectl 명령 등)",
        "metric_hint": {            # metric_observed일 때만
          "namespace": "...",
          "metric_name": "...",
          "statistic": "...",
          "comparison": "...",
          "direction": "increase|decrease",
          "typical_dimensions": ["..."]
        },
        "fallback": "메트릭 없을 때 대안 방법"
      },
    ],
    "reaction_confirmed": [         # Phase 3: Agent 반응 확인 (고정)
      "investigation_started: Agent가 조사 시작",
      "investigation_completed: Agent가 근본 원인 분석 완료",
    ],
  },
  "requires": "...",                # 실행에 필요한 사전 정보
  "applicable_when": "...",         # 이 장애가 유효한 조건
  "investigation_prompt": "...",    # Agent에게 보낼 조사 프롬프트
  "detection_challenge": "...",     # Agent가 넘어야 할 진단 난이도
  "proactive_question": "...",      # 사전 예방 질문 (proactive 모드)
  "restore_mechanism": [...],       # 복원 방법
}
```

### observation_signals 작성 규칙
1. effect_type 분류 필수:
   - infra_state: 인프라에서 직접 읽을 수 있는 변화 (앱 행동 무관, 보장됨)
   - metric_observed: 메트릭으로 확인하는 변화 (인프라 필요, 앱 무관)
   - app_dependent: 앱 에러 핸들링에 의존 (보장 불가)
2. effect_observed에 infra_state signal 최소 1개 필수
3. Crash 가정 금지: trigger가 기계적으로 kill하는 경우 외 pod crash 가정 불가
4. metric_observed는 metric_hint + fallback 필수
5. app_dependent는 단독 사용 금지 (infra_state/metric_observed와 병행)
6. trigger_active: 장애 주입 확인 (1-2개). string 또는 object 형식
7. reaction_confirmed: 항상 investigation_started + investigation_completed (고정, string)
"""

FAILURE_MODES = [
    {
        "id": "FM-01",
        "name": "Network Isolation",
        "layer": "infrastructure",
        "description": "서비스 간 또는 서비스-외부 리소스 간 네트워크 통신 차단. "
                       "Security Group 규칙 제거 또는 NACL deny 규칙 추가로 특정 포트/CIDR 차단.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "aws ec2 revoke-security-group-ingress",
            "aws ec2 create-network-acl-entry (deny)",
        ],
        "observation_signals": {
            "trigger_active": [
                "connectivity_blocked: 대상 포트/경로로의 네트워크 연결이 차단된 상태 (연결 거부 또는 타임아웃). 확인 시 실제 연결 시도(curl/wget) 또는 SG/NACL 규칙 조회로 검증",
                "security_rule_changed: 네트워크 접근 규칙(SG/NACL/NetworkPolicy)이 변경됨. describe-security-groups 또는 describe-network-acls로 규칙 존재 확인",
            ],
            "effect_observed": [
                {
                    "signal": "connectivity_test_fail",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "호출자에서 대상 서비스로의 연결 시도가 실패 (timeout/refused). 앱 crash 여부와 무관하게 네트워크 차단 자체를 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- wget --timeout=3 -qO- http://<target-svc>:<port>/ 2>&1, expected=timed out|Connection refused|download timed out",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스 간 네트워크 통신 경로 (SG ID, 포트, CIDR 또는 서브넷)",
        "applicable_when": "서비스 간 네트워크 통신 경로가 존재하고 SG/NACL로 제어될 때",
        "investigation_prompt": "{target_service} 서비스와의 네트워크 연결 상태를 점검하고, "
                                "통신 실패의 원인을 분석해주세요.",
        "detection_challenge": "앱 로그에는 연결 타임아웃만 보이고, SG/NACL 변경은 CloudTrail에서만 확인 가능. "
                               "Agent가 인프라 변경 이력까지 추적해야 근본 원인 도달.",
        "proactive_question": "현재 SG/NACL 규칙 중 단일 규칙 제거/추가 시 서비스 중단을 유발하는 것은?",
        "restore_mechanism": [
            "aws ec2 authorize-security-group-ingress",
            "aws ec2 delete-network-acl-entry",
        ],
    },
    {
        "id": "FM-02",
        "name": "Compute Kill",
        "layer": "infrastructure",
        "description": "실행 중인 컴퓨트 리소스(EC2, ECS task, EKS pod, Lambda)를 강제 종료. "
                       "Spot 회수, AZ 장애, OOM 크래시 등 실제 장애를 시뮬레이션.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "FIS aws:ec2:terminate-instances",
            "FIS aws:ecs:stop-task",
            "FIS aws:eks:terminate-nodegroup-instances",
            "aws lambda put-function-concurrency (0으로 설정)",
        ],
        "observation_signals": {
            "trigger_active": [
                "container_not_running: 대상 컨테이너/프로세스가 비정상 상태 (종료됨, 재시작 중)",
                "instance_terminated: 대상 인스턴스/task가 종료 상태",
            ],
            "effect_observed": [
                {
                    "signal": "available_replicas_decreased",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "정상 Ready 인스턴스/pod 수가 desired보다 감소. trigger가 기계적으로 kill하므로 100% 보장",
                    "verification_hint": "kubectl_check: kubectl get deploy/<target> -n <ns> -o jsonpath='{.status.availableReplicas}', expected=값이 desired보다 작거나 pod 상태가 비정상",
                },
                {
                    "signal": "restart_count_increase",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "컨테이너 재시작 횟수 증가. trigger가 기계적 kill(terminate/delete)이므로 재시작 보장됨",
                    "verification_hint": "kubectl_check: kubectl get pod -l app=<target> -n <ns> -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}', expected=1 이상",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "컴퓨트 리소스 식별자 (인스턴스 ID, 클러스터/서비스명, 함수명)",
        "applicable_when": "서비스가 컴퓨트 리소스에서 실행 중일 때 (모든 환경)",
        "investigation_prompt": "{target_service} 서비스의 실행 상태를 점검하고, "
                                "가용성 문제가 있다면 원인을 분석해주세요.",
        "detection_challenge": "단일 인스턴스/task일 경우 전체 서비스 중단. "
                               "Auto Scaling이 있으면 자동 복구되지만 복구 시간 동안 영향 발생.",
        "proactive_question": "단일 인스턴스/task로 실행 중이거나 Auto Scaling이 없는 서비스는?",
        "restore_mechanism": [
            "Auto Scaling / Scheduler 자동 복구 (검증 대상)",
            "aws lambda put-function-concurrency (원래 값)",
        ],
    },
    {
        "id": "FM-03",
        "name": "Dependency Blackhole",
        "layer": "infrastructure",
        "description": "외부 의존성(RDS, ElastiCache, S3, DynamoDB 등) 접근을 차단. "
                       "DB failover, 캐시 노드 교체, S3 throttling 등 실제 장애를 시뮬레이션.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "SG로 DB/캐시 포트 차단 (aws ec2 revoke-security-group-ingress)",
            "FIS aws:rds:reboot-db-instances (failover 포함)",
            "FIS aws:network:disrupt-connectivity (서브넷 간)",
        ],
        "observation_signals": {
            "trigger_active": [
                "dependency_unreachable: 대상 의존 서비스(DB/캐시/스토리지)로의 연결이 실패 상태. scale 0인 경우 available replicas=0으로 확인",
                "connection_refused: 해당 포트/엔드포인트에 대한 신규 연결 거부",
            ],
            "effect_observed": [
                {
                    "signal": "dependency_connection_refused",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "호출자에서 의존 서비스로의 연결 시도가 거부됨 (replicas=0이면 endpoint 없음, SG 차단이면 timeout). 앱 crash 여부와 무관하게 의존성 도달 불가 자체를 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- sh -c 'echo PING | nc -w 3 <dep-svc> <port> || echo CONNECTION_FAILED', expected=CONNECTION_FAILED|refused",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스가 의존하는 외부 리소스 목록 (RDS endpoint, ElastiCache, S3 bucket 등)",
        "applicable_when": "서비스가 AWS 관리형 서비스(RDS, ElastiCache, DynamoDB, S3 등)에 의존할 때",
        "investigation_prompt": "{target_service} 서비스의 외부 의존성 연결 상태를 점검하고, "
                                "접근 실패가 있다면 원인과 영향 범위를 분석해주세요.",
        "detection_challenge": "커넥션 풀이 있으면 기존 연결은 유지되고 새 연결만 실패 → 간헐적 에러. "
                               "DB failover는 30초 내 복구되지만 앱의 재연결 로직에 따라 영향 다름.",
        "proactive_question": "외부 의존성 연결 수가 max_connections 대비 높거나, "
                              "failover 설정이 안 된 서비스는?",
        "restore_mechanism": [
            "aws ec2 authorize-security-group-ingress (포트 복원)",
            "RDS/ElastiCache 자동 복구 대기",
        ],
    },
    {
        "id": "FM-04",
        "name": "Resource Pressure",
        "layer": "infrastructure",
        "description": "호스트의 CPU/메모리에 인위적 부하를 가해 리소스 경쟁 유발. "
                       "트래픽 급증, noisy neighbor, 리소스 부족 등 실제 장애를 시뮬레이션.",
        "trigger_mode": "either",
        "trigger_mechanism": [
            "FIS AWSFIS-Run-CPU-Stress (SSM document)",
            "FIS AWSFIS-Run-Memory-Stress (SSM document)",
        ],
        "observation_signals": {
            "trigger_active": [
                "resource_utilization_high: 대상 호스트의 CPU 또는 메모리 사용률이 임계치 초과",
                "stress_process_running: 부하 생성 프로세스(stress-ng 등)가 실행 중",
            ],
            "effect_observed": [
                {
                    "signal": "resource_limit_applied",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "pod의 resource limits/requests가 의도한 값으로 적용됨 (spec에서 직접 확인). CPU 제한 시나리오에서 100% 보장",
                    "verification_hint": "kubectl_check: kubectl get pod -l app=<target> -o jsonpath='{.items[0].spec.containers[0].resources.limits.cpu}', expected=<설정한_limit_값>",
                },
                {
                    "signal": "cpu_throttling",
                    "effect_type": "metric_observed",
                    "confidence": "medium",
                    "description": "CPU throttling 비율 증가. 메트릭 인프라(ContainerInsights)가 있을 때만 확인 가능",
                    "metric_hint": {
                        "namespace": "ContainerInsights",
                        "metric_name": "pod_cpu_utilization_over_pod_limit",
                        "statistic": "Average",
                        "comparison": "GreaterThanThreshold",
                        "direction": "increase",
                        "typical_dimensions": ["ClusterName", "Namespace", "PodName"],
                    },
                    "fallback": "kubectl_check: kubectl top pod -l app=<target>으로 CPU 사용량 확인, 또는 resource_limit_applied만으로 충분",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "대상 호스트/인스턴스 식별자, 현재 리소스 사용률",
        "applicable_when": "서비스가 EC2 기반 컴퓨트(EKS node, ECS EC2, EC2 직접)에서 실행될 때",
        "investigation_prompt": "{target_service} 서비스가 실행되는 호스트의 리소스 상태(CPU, 메모리)를 "
                                "점검하고, 성능 저하 징후가 있는지 분석해주세요.",
        "detection_challenge": "CPU throttling은 앱 로그에 직접 나타나지 않고 응답 지연으로만 표현됨. "
                               "메모리 압박은 swap 사용 → 극심한 지연 → OOM 순서로 진행.",
        "proactive_question": "현재 CPU/메모리 사용률이 70% 이상이거나 증가 추세인 호스트는?",
        "restore_mechanism": [
            "FIS 실험 종료 (duration 기반 자동 복구)",
        ],
    },
    {
        "id": "FM-05",
        "name": "Permission Revoke",
        "layer": "infrastructure",
        "description": "IAM 정책 변경으로 서비스의 AWS API 호출 권한을 제거. "
                       "IAM policy 실수, STS 토큰 만료 등 실제 장애를 시뮬레이션.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "aws iam put-role-policy (explicit deny 추가)",
            "aws iam delete-role-policy (inline policy 제거)",
        ],
        "observation_signals": {
            "trigger_active": [
                "permission_denied: 대상 서비스의 API 호출이 권한 거부(AccessDenied/Unauthorized) 상태",
                "policy_changed: IAM 정책이 변경됨 (CloudTrail 이벤트)",
            ],
            "effect_observed": [
                {
                    "signal": "policy_deny_applied",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "IAM policy에 explicit deny가 적용됨. get-role-policy 또는 simulate-principal-policy로 직접 확인 가능",
                    "verification_hint": "aws_cli: aws iam simulate-principal-policy --policy-source-arn <role-arn> --action-names <action> | jq '.EvaluationResults[0].EvalDecision', expected=implicitDeny|explicitDeny",
                },
                {
                    "signal": "api_call_access_denied",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "실제 AWS API 호출 시 AccessDenied 에러 반환. exec로 aws cli 호출하여 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<target> -n <ns> -c <container> -- aws <service> <action> 2>&1, expected=AccessDenied|UnauthorizedAccess",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스가 사용하는 IAM role ARN과 호출하는 AWS API 목록 (CloudTrail에서 발견 가능)",
        "applicable_when": "서비스가 IAM role을 통해 AWS API(S3, DynamoDB, SQS 등)를 호출할 때",
        "investigation_prompt": "{target_service} 서비스의 AWS API 호출 상태를 점검하고, "
                                "권한 오류(AccessDenied)가 있다면 원인을 분석해주세요.",
        "detection_challenge": "AccessDenied 에러는 앱 로그에 나타나지만 원인(어떤 policy가 변경됐는지)은 "
                               "CloudTrail IAM 이벤트를 역추적해야 발견 가능.",
        "proactive_question": "최근 CloudTrail에서 AccessDenied 이벤트가 있는 서비스는? "
                              "IAM policy가 최근 변경된 role은?",
        "restore_mechanism": [
            "aws iam put-role-policy (deny 제거 또는 원래 policy 복원)",
            "aws iam attach-role-policy (제거된 policy 재연결)",
        ],
    },
    {
        "id": "FM-06",
        "name": "DNS Disruption",
        "layer": "infrastructure",
        "description": "Route53 레코드 변조 또는 DNS 해석 경로 차단으로 서비스 간 이름 해석 실패. "
                       "DNS 장애, TTL 만료 후 잘못된 IP, internal DNS 오류 등을 시뮬레이션.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "aws route53 change-resource-record-sets (레코드를 잘못된 IP로 변경)",
            "aws route53 change-resource-record-sets (레코드 삭제)",
        ],
        "observation_signals": {
            "trigger_active": [
                "dns_resolution_failed: 대상 도메인의 DNS 해석이 실패하거나 잘못된 IP 반환",
                "dns_record_changed: DNS 레코드가 변경/삭제됨",
            ],
            "effect_observed": [
                {
                    "signal": "dns_resolution_mismatch",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "DNS 해석 결과가 기대값과 불일치 (잘못된 IP 반환 또는 NXDOMAIN). dig/nslookup으로 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- nslookup <domain> 2>&1, expected=NXDOMAIN|server can't find|잘못된_IP",
                },
                {
                    "signal": "connectivity_test_fail_dns",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "DNS 문제로 인한 연결 실패. wget/curl로 도메인 기반 접속 시도하여 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- wget --timeout=3 -qO- http://<domain>:<port>/ 2>&1, expected=Name or service not known|resolve|FAILED",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스가 사용하는 DNS 레코드 목록 (Route53 hosted zone, 레코드명)",
        "applicable_when": "서비스 간 통신이 DNS 기반(Route53 private hosted zone, service discovery)일 때",
        "investigation_prompt": "{target_service} 서비스의 DNS 해석 상태를 점검하고, "
                                "이름 해석 실패가 있다면 원인을 분석해주세요.",
        "detection_challenge": "DNS 캐싱으로 인해 변경 후 즉시 영향이 나타나지 않을 수 있음. "
                               "TTL 만료 후 갑자기 장애 발생하면 시점 추적이 어려움.",
        "proactive_question": "비정상적으로 높은 TTL을 가진 DNS 레코드나, "
                              "사용되지 않는 레코드가 있는지 확인해주세요.",
        "restore_mechanism": [
            "aws route53 change-resource-record-sets (원래 레코드 복원)",
        ],
    },
    {
        "id": "FM-07",
        "name": "AZ Failure",
        "layer": "infrastructure",
        "description": "특정 가용영역(AZ)의 네트워크 연결을 차단하여 AZ 수준 장애를 시뮬레이션. "
                       "2024 Tokyo AZ 장애 같은 대규모 인프라 장애를 재현.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "FIS aws:network:disrupt-connectivity (특정 AZ 서브넷 대상)",
        ],
        "observation_signals": {
            "trigger_active": [
                "az_connectivity_lost: 특정 AZ 내 리소스의 네트워크 연결이 차단됨",
                "instances_unhealthy_in_az: 해당 AZ의 인스턴스/노드가 비정상 상태",
            ],
            "effect_observed": [
                {
                    "signal": "nodes_not_ready_in_az",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "특정 AZ의 노드가 NotReady 상태로 전환. kubectl get nodes로 직접 확인",
                    "verification_hint": "kubectl_check: kubectl get nodes -l topology.kubernetes.io/zone=<az> -o jsonpath='{.items[*].status.conditions[?(@.type==\"Ready\")].status}', expected=False 포함",
                },
                {
                    "signal": "pods_disrupted_in_az",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "해당 AZ 노드에서 실행 중이던 pod가 비정상(Pending/Unknown) 전환. 가용 replicas 감소",
                    "verification_hint": "kubectl_check: kubectl get pods -n <ns> --field-selector spec.nodeName=<az-node> -o jsonpath='{.items[*].status.phase}', expected=Pending|Unknown 포함",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스가 배포된 AZ 목록, 서브넷 ID, multi-AZ 구성 여부",
        "applicable_when": "서비스가 특정 AZ에 배포되어 있고, multi-AZ 분산 여부를 검증할 때",
        "investigation_prompt": "현재 전체 서비스의 가용성을 점검하고, "
                                "특정 AZ에서 장애가 발생했는지, 영향 범위는 어디까지인지 분석해주세요.",
        "detection_challenge": "multi-AZ 서비스는 자동 failover로 영향 최소화되지만, "
                               "single-AZ 의존 리소스(EBS, RDS single-AZ)가 있으면 전체 중단 가능.",
        "proactive_question": "single-AZ에만 배치된 리소스(인스턴스, RDS, EBS)가 있는지 확인해주세요.",
        "restore_mechanism": [
            "FIS 실험 종료 (duration 기반 자동 복구)",
        ],
    },
    {
        "id": "FM-08",
        "name": "Config Tamper",
        "layer": "application",
        "description": "앱이 참조하는 설정값(Secrets Manager, Parameter Store, 환경변수)을 변조하여 "
                       "설정 오류를 유발. Secret rotation 실패, 잘못된 설정 배포 등을 시뮬레이션.",
        "trigger_mode": "either",
        "trigger_mechanism": [
            "aws secretsmanager put-secret-value (잘못된 값)",
            "aws ssm put-parameter (잘못된 설정)",
            "aws ecs update-service / kubectl set env (환경변수 변경)",
        ],
        "observation_signals": {
            "trigger_active": [
                "config_value_changed: 설정값(Secret/Parameter/환경변수)이 변경됨",
                "service_restarted: 설정 반영을 위해 서비스가 재시작됨 (또는 캐시 만료 후 반영)",
            ],
            "effect_observed": [
                {
                    "signal": "config_value_applied",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "변경된 설정값이 실제 컨테이너/서비스에 반영됨. 환경변수/ConfigMap 직접 조회로 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<target> -n <ns> -c <container> -- env | grep <CONFIG_KEY>, expected=변경된_값 또는 kubectl get cm/<configmap> -o jsonpath='{.data.<key>}'",
                },
                {
                    "signal": "connection_test_fail",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "변경된 설정(endpoint/port/credential)으로 인해 실제 연결 시도가 실패. exec로 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<target> -n <ns> -c <container> -- wget --timeout=3 -qO- http://<new-endpoint>:<port>/ 2>&1, expected=FAILED|refused|resolve",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스가 참조하는 Secret/Parameter 이름 (CloudTrail GetSecretValue, GetParameter 이벤트로 발견)",
        "applicable_when": "서비스가 Secrets Manager, Parameter Store, 또는 환경변수로 설정을 관리할 때",
        "investigation_prompt": "{target_service} 서비스의 설정 상태를 점검하고, "
                                "최근 설정 변경이 서비스 동작에 영향을 미치는지 분석해주세요.",
        "detection_challenge": "설정 변경은 앱 재시작 또는 캐시 만료 후에야 영향 발현. "
                               "앱 로그에는 설정 자체가 아닌 결과적 에러만 기록됨.",
        "proactive_question": "rotation 주기가 지난 Secret이 있거나, "
                              "최근 변경된 Parameter/Secret이 있는지 확인해주세요.",
        "restore_mechanism": [
            "aws secretsmanager put-secret-value (원래 값 복원)",
            "aws ssm put-parameter (원래 값 복원)",
        ],
    },
    {
        "id": "FM-09",
        "name": "Deploy Failure",
        "layer": "application",
        "description": "잘못된 이미지/task definition/코드 패키지로 배포하여 서비스 시작 실패를 유발. "
                       "깨진 빌드 배포, 호환 안 되는 런타임 버전 등을 시뮬레이션.",
        "trigger_mode": "reactive",
        "trigger_mechanism": [
            "aws ecs update-service --task-definition (잘못된 이미지)",
            "kubectl set image (존재하지 않는 태그)",
            "aws lambda update-function-code (잘못된 패키지)",
        ],
        "observation_signals": {
            "trigger_active": [
                "container_image_pull_failed: 컨테이너 이미지 다운로드 실패 (ImagePullBackOff/ErrImagePull)",
                "task_start_failed: 새 인스턴스/task/container 시작 실패 상태",
            ],
            "effect_observed": [
                {
                    "signal": "image_pull_failed",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "새 pod가 ImagePullBackOff/ErrImagePull 상태. 존재하지 않는 이미지 태그를 설정했으므로 100% 보장",
                    "verification_hint": "kubectl_check: kubectl get pod -l app=<target> -n <ns> -o jsonpath='{.items[?(@.status.phase!=\"Running\")].status.containerStatuses[0].state.waiting.reason}', expected=ImagePullBackOff|ErrImagePull",
                },
                {
                    "signal": "available_replicas_decreased",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "availableReplicas가 desired(spec.replicas)보다 감소. 새 pod가 시작 불가하므로 rolling update가 진행되지 않음",
                    "verification_hint": "kubectl_check: kubectl get deploy/<target> -n <ns> -o jsonpath='{.status.availableReplicas}', expected=desired보다 작은 값 또는 unavailableReplicas > 0",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "현재 배포된 서비스의 이미지/task-def/코드 정보",
        "applicable_when": "서비스가 컨테이너 이미지, Lambda 패키지, 또는 배포 가능한 아티팩트로 실행될 때",
        "investigation_prompt": "{target_service} 서비스의 배포 상태를 점검하고, "
                                "시작 실패가 있다면 원인을 분석해주세요.",
        "detection_challenge": "Rolling update 전략이면 기존 인스턴스는 유지되어 즉시 장애가 안 보임. "
                               "새 인스턴스만 실패하면서 점진적으로 처리량 감소.",
        "proactive_question": "현재 배포된 이미지와 latest 태그 사이 차이가 있는 서비스는? "
                              "rollback 가능한 이전 버전이 존재하는지 확인해주세요.",
        "restore_mechanism": [
            "aws ecs update-service --task-definition (이전 버전)",
            "kubectl rollout undo",
            "aws lambda update-function-code (이전 버전)",
        ],
    },
    {
        "id": "FM-10",
        "name": "Endpoint Abuse",
        "layer": "application",
        "description": "외부 접근 가능한 엔드포인트에 비정상 요청(잘못된 페이로드, 대용량, 대량 요청)을 "
                       "보내 에러율 상승/리소스 고갈 유발. DDoS, 잘못된 클라이언트, 크롤러 폭주 등을 시뮬레이션.",
        "trigger_mode": "either",
        "trigger_mechanism": [
            "Lambda/스크립트로 대상 endpoint에 비정상 요청 반복 전송",
            "aws application-autoscaling register-scalable-target (스케일링 비활성화 후 부하)",
        ],
        "observation_signals": {
            "trigger_active": [
                "request_volume_spike: 대상 엔드포인트로의 요청량이 비정상적으로 급증",
                "malformed_request_detected: 비정상 요청(큰 페이로드, 잘못된 형식)이 유입됨",
            ],
            "effect_observed": [
                {
                    "signal": "error_response_rate",
                    "effect_type": "metric_observed",
                    "confidence": "medium",
                    "description": "5xx/4xx 에러 응답 비율 급증. ApplicationSignals 또는 ALB 메트릭으로 확인",
                    "metric_hint": {
                        "namespace": "AWS/ApplicationELB",
                        "metric_name": "HTTPCode_Target_5XX_Count",
                        "statistic": "Sum",
                        "comparison": "GreaterThanThreshold",
                        "direction": "increase",
                        "typical_dimensions": ["TargetGroup", "LoadBalancer"],
                    },
                    "fallback": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -- wget --timeout=3 -qO- http://<target-svc>:<port>/<path> 2>&1로 응답 코드 직접 확인",
                },
                {
                    "signal": "resource_utilization_high",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "대상 pod의 CPU/메모리 사용률이 limit에 근접. kubectl top으로 직접 확인",
                    "verification_hint": "kubectl_check: kubectl top pod -l app=<target> -n <ns>, expected=CPU 또는 Memory 사용량이 limit 대비 높음",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "외부 접근 가능한 엔드포인트 URL (ALB, API Gateway, CloudFront에서 발견)",
        "applicable_when": "외부에서 접근 가능한 HTTP/HTTPS 엔드포인트가 있을 때",
        "investigation_prompt": "{target_service} 서비스의 엔드포인트 상태를 점검하고, "
                                "비정상 트래픽이나 에러율 급증이 있는지 분석해주세요.",
        "detection_challenge": "정상 요청과 비정상 요청이 섞이면 에러율이 점진적으로 올라감. "
                               "어떤 유형의 요청이 문제인지 식별해야 함.",
        "proactive_question": "현재 요청 처리량 대비 auto scaling 상한이 충분한지, "
                              "rate limiting이 설정되어 있는지 확인해주세요.",
        "restore_mechanism": [
            "비정상 요청 중단",
            "aws application-autoscaling register-scalable-target (스케일링 재활성화)",
        ],
    },
    # ── FM-12~FM-22: Business Logic / General Application ────────────
    {
        "id": "FM-12",
        "name": "Database Performance Degradation",
        "layer": "data",
        "description": "RDS 파라미터 그룹 변경(buffer_pool_size 축소, max_connections 축소) 또는 "
                       "DynamoDB 프로비전 용량을 극단적으로 축소하여 DB 성능 급감. "
                       "Noisy neighbor, 인덱스 누락, 파라미터 드리프트 등을 시뮬레이션.",
        "trigger_mode": "proactive",
        "trigger_mechanism": [
            "aws rds modify-db-parameter-group (buffer_pool_size, query_cache_size 축소)",
            "aws dynamodb update-table --provisioned-throughput ReadCapacityUnits=1,WriteCapacityUnits=1",
            "aws application-autoscaling register-scalable-target (DynamoDB auto-scaling 비활성화)",
        ],
        "observation_signals": {
            "trigger_active": [
                "db_parameter_changed: DB 파라미터/용량 설정이 변경됨",
                "throughput_capacity_reduced: 프로비전된 처리 용량이 축소됨",
            ],
            "effect_observed": [
                {
                    "signal": "db_parameter_changed_confirmed",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "DB 파라미터/용량이 의도한 값으로 변경됨. describe-db-parameters 또는 describe-table로 직접 확인",
                    "verification_hint": "aws_cli: aws rds describe-db-parameters --db-parameter-group-name <group> --query 'Parameters[?ParameterName==`<param>`].ParameterValue' 또는 aws dynamodb describe-table --table-name <table> --query 'Table.ProvisionedThroughput', expected=변경된_값",
                },
                {
                    "signal": "throttling_events",
                    "effect_type": "metric_observed",
                    "confidence": "medium",
                    "description": "DynamoDB ThrottledRequests 또는 RDS DatabaseConnections 급증",
                    "metric_hint": {
                        "namespace": "AWS/DynamoDB",
                        "metric_name": "ThrottledRequests",
                        "statistic": "Sum",
                        "comparison": "GreaterThanThreshold",
                        "direction": "increase",
                        "typical_dimensions": ["TableName"],
                    },
                    "fallback": "aws_cli: aws dynamodb describe-table --query 'Table.ProvisionedThroughput'로 현재 용량 확인",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "RDS 인스턴스/파라미터 그룹명 또는 DynamoDB 테이블명, 현재 프로비전 용량",
        "applicable_when": "서비스가 RDS 또는 DynamoDB를 사용하고 성능 관련 파라미터가 변경 가능할 때",
        "investigation_prompt": "{target_service} 서비스의 데이터베이스 응답 시간과 쿼리 성능을 점검하고, "
                                "DB 수준 병목이 있는지 분석해주세요.",
        "detection_challenge": "앱 레벨에서는 응답 지연으로만 나타나고, DB 파라미터 변경은 CloudTrail에서만 확인. "
                               "DynamoDB 스로틀링은 ConsumedReadCapacityUnits 메트릭을 봐야 식별 가능.",
        "proactive_question": "RDS 파라미터 그룹이 최근 변경되었거나, "
                              "DynamoDB 테이블의 ThrottledRequests 메트릭이 0 이상인 테이블은?",
        "restore_mechanism": [
            "aws rds modify-db-parameter-group (원래 파라미터 복원) + reboot",
            "aws dynamodb update-table --provisioned-throughput (원래 용량 복원)",
        ],
    },
    {
        "id": "FM-15",
        "name": "Cache Invalidation Failure",
        "layer": "application",
        "description": "ElastiCache 노드 강제 재부팅 또는 SG로 캐시 포트를 일시 차단하여 "
                       "캐시 미스 폭증, thundering herd, 스테일 데이터 서빙을 유발. "
                       "캐시 노드 교체 후 cold start, 캐시 키 충돌 등을 시뮬레이션.",
        "trigger_mode": "either",
        "trigger_mechanism": [
            "aws elasticache reboot-cache-cluster --cache-cluster-id (전체 캐시 플러시)",
            "aws elasticache modify-cache-cluster --cache-node-ids-to-remove (특정 노드 제거)",
            "SG로 ElastiCache 포트(6379/11211) 일시 차단 후 해제 (cold start 유도)",
        ],
        "observation_signals": {
            "trigger_active": [
                "cache_node_unavailable: 캐시 노드가 비정상(재부팅 중, 연결 불가) 상태",
                "cache_connection_failed: 서비스에서 캐시로의 연결이 실패/거부됨",
            ],
            "effect_observed": [
                {
                    "signal": "cache_connection_test_fail",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "서비스에서 캐시로의 연결이 거부/타임아웃. exec로 캐시 포트 연결 시도하여 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- sh -c 'echo PING | nc -w 3 <cache-endpoint> <port> || echo CONNECTION_FAILED', expected=CONNECTION_FAILED|timed out",
                },
                {
                    "signal": "cache_hit_rate_drop",
                    "effect_type": "metric_observed",
                    "confidence": "medium",
                    "description": "캐시 히트율 급감. ElastiCache CacheHitRate 메트릭으로 확인",
                    "metric_hint": {
                        "namespace": "AWS/ElastiCache",
                        "metric_name": "CacheHitRate",
                        "statistic": "Average",
                        "comparison": "LessThanThreshold",
                        "direction": "decrease",
                        "typical_dimensions": ["CacheClusterId"],
                    },
                    "fallback": "kubectl_check: kubectl exec로 캐시 키 조회 시도 (redis-cli GET <key>), expected=nil 또는 connection refused",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "ElastiCache 클러스터 ID, 노드 목록, 서비스가 사용하는 SG ID",
        "applicable_when": "서비스가 ElastiCache(Redis/Memcached)를 캐싱 레이어로 사용할 때",
        "investigation_prompt": "{target_service} 서비스의 캐시 히트율과 응답 시간을 점검하고, "
                                "캐시 미스 급증이나 스테일 데이터 서빙 징후가 있는지 분석해주세요.",
        "detection_challenge": "캐시 미스 후 DB 직접 조회로 폴백하면 앱은 정상 동작하지만 DB 부하 급증. "
                               "thundering herd는 캐시 복구 후에도 DB 커넥션 고갈로 이어질 수 있음.",
        "proactive_question": "CacheHitRate가 급감하거나 CurrConnections가 급증한 ElastiCache 클러스터는? "
                              "캐시 노드 교체 이력이 최근에 있었는지 확인해주세요.",
        "restore_mechanism": [
            "aws ec2 authorize-security-group-ingress (캐시 포트 복원)",
            "ElastiCache 자동 복구 대기 (재부팅 후 warm-up)",
        ],
    },
    {
        "id": "FM-18",
        "name": "Auto-Scaling Misconfiguration",
        "layer": "application",
        "description": "Auto Scaling 최대 용량을 현재 최소값으로 고정하여 스케일아웃 불가 상태 생성. "
                       "트래픽 급증 시 확장 실패, 스케일인 과다(flapping), "
                       "잘못된 메트릭 기반 스케일링 등을 시뮬레이션.",
        "trigger_mode": "either",
        "trigger_mechanism": [
            "aws application-autoscaling register-scalable-target --max-capacity 1",
            "aws autoscaling update-auto-scaling-group --max-size 1 --desired-capacity 1",
            "kubectl patch hpa -p '{\"spec\":{\"maxReplicas\":1}}'",
        ],
        "observation_signals": {
            "trigger_active": [
                "scaling_limit_reached: Auto Scaling 최대 용량이 현재 인스턴스 수와 동일 (확장 불가)",
                "scaling_policy_changed: 스케일링 정책/한도가 변경됨",
            ],
            "effect_observed": [
                {
                    "signal": "scaling_max_capacity_applied",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "HPA/ASG maxReplicas가 의도한 값(1)으로 적용됨. 스펙 직접 조회로 확인",
                    "verification_hint": "kubectl_check: kubectl get hpa <hpa-name> -n <ns> -o jsonpath='{.spec.maxReplicas}', expected=1 또는 aws autoscaling describe-auto-scaling-groups --query 'AutoScalingGroups[0].MaxSize'",
                },
                {
                    "signal": "current_at_max",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "현재 replicas가 maxReplicas와 동일 (확장 불가 상태). HPA status에서 확인",
                    "verification_hint": "kubectl_check: kubectl get hpa <hpa-name> -n <ns> -o jsonpath='{.status.currentReplicas}', expected=maxReplicas와 동일한 값",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "Auto Scaling Group명, ECS 서비스명, 또는 K8s HPA 이름, 현재 트래픽 수준",
        "applicable_when": "서비스에 Auto Scaling(EC2 ASG, ECS service auto-scaling, K8s HPA) 정책이 설정되어 있을 때",
        "investigation_prompt": "{target_service} 서비스의 스케일링 상태를 점검하고, "
                                "부하 증가에도 확장이 안 되는 문제가 있는지 분석해주세요.",
        "detection_challenge": "평상시에는 문제 없이 보이지만 부하 급증 시 확장 불가로 서비스 저하 발생. "
                               "ASG/HPA 이벤트 로그에서 FailedScaleUp을 확인해야 하며, "
                               "앱 레벨에서는 응답 지연/타임아웃으로만 나타남.",
        "proactive_question": "현재 인스턴스/task 수가 max capacity에 근접하거나, "
                              "최근 ScaleUp 이벤트가 실패한 ASG/ECS 서비스/HPA는?",
        "restore_mechanism": [
            "aws application-autoscaling register-scalable-target --max-capacity (원래 값)",
            "aws autoscaling update-auto-scaling-group --max-size (원래 값)",
            "kubectl patch hpa -p '{\"spec\":{\"maxReplicas\":(원래 값)}}'",
        ],
    },
    {
        "id": "FM-19",
        "name": "Observability Blind Spot",
        "layer": "observability",
        "description": "CloudWatch 알람 삭제, X-Ray 샘플링률 0% 변경, Log Group 보존 정책 변경으로 "
                       "모니터링 능력을 무력화한 뒤 다른 장애를 주입. "
                       "알람 누락, 로그 손실, 트레이스 불가 상황에서 Agent가 대체 경로로 진단할 수 있는지 검증.",
        "trigger_mode": "proactive",
        "trigger_mechanism": [
            "aws cloudwatch delete-alarms --alarm-names (알람 삭제)",
            "aws xray update-sampling-rule --sampling-rule-update FixedRate=0 (트레이싱 비활성화)",
            "aws logs put-retention-policy --retention-in-days 1 (로그 보존 최소화)",
        ],
        "observation_signals": {
            "trigger_active": [
                "monitoring_disabled: 알람/트레이스/로그 수집이 비활성화 또는 삭제됨",
                "observability_gap: 특정 시간대/서비스의 모니터링 데이터가 수집되지 않음",
            ],
            "effect_observed": [
                {
                    "signal": "alarm_absent",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "기존 알람이 삭제되어 describe-alarms에서 조회되지 않음. AWS CLI로 직접 확인",
                    "verification_hint": "aws_cli: aws cloudwatch describe-alarms --alarm-names <alarm-name> --query 'MetricAlarms', expected=빈 배열 [] 또는 알람 없음",
                },
                {
                    "signal": "sampling_rate_zero",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "X-Ray 샘플링률이 0%로 설정됨. get-sampling-rules로 직접 확인",
                    "verification_hint": "aws_cli: aws xray get-sampling-rules --query 'SamplingRuleRecords[?SamplingRule.RuleName==`<rule>`].SamplingRule.FixedRate', expected=0 또는 0.0",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료 (대체 경로 활용)",
            ],
        },
        "requires": "CloudWatch 알람 목록, X-Ray 샘플링 규칙명, CloudWatch Log Group 목록",
        "applicable_when": "서비스에 CloudWatch 알람, X-Ray 트레이스, 또는 CloudWatch Logs가 설정되어 있을 때",
        "investigation_prompt": "모니터링 데이터가 일부 누락된 상태에서 {target_service} 서비스의 "
                                "정상 동작 여부를 대체 수단을 활용하여 분석해주세요.",
        "detection_challenge": "모니터링 도구 자체가 무력화되어 일반적인 진단 경로가 차단됨. "
                               "Agent는 CloudTrail, VPC Flow Logs, 직접 API 호출 등 "
                               "대체 데이터 소스를 활용해야 함.",
        "proactive_question": "최근 삭제되거나 비활성화된 CloudWatch 알람이 있는지, "
                              "X-Ray 샘플링률이 비정상적으로 낮은 서비스가 있는지 확인해주세요.",
        "restore_mechanism": [
            "aws cloudwatch put-metric-alarm (알람 재생성)",
            "aws xray update-sampling-rule --sampling-rule-update FixedRate=(원래 값)",
            "aws logs put-retention-policy --retention-in-days (원래 값)",
        ],
    },
    {
        "id": "FM-21",
        "name": "Storage Pressure",
        "layer": "infrastructure",
        "description": "FIS로 EBS I/O를 중단하거나 디스크를 채워 스토리지 압박을 유발. "
                       "EBS 볼륨 가득 참, gp2/gp3 IOPS 고갈, EFS 스루풋 병목 등을 시뮬레이션.",
        "trigger_mode": "either",
        "trigger_mechanism": [
            "FIS aws:ebs:pause-volume-io (EBS I/O 중단)",
            "FIS AWSFIS-Run-Disk-Fill (SSM document — 디스크 채우기)",
            "aws efs update-file-system --throughput-mode provisioned "
            "--provisioned-throughput-in-mibps 1 (EFS 스루풋 최소화)",
        ],
        "observation_signals": {
            "trigger_active": [
                "disk_io_blocked: 디스크 I/O 작업이 중단/지연됨 (읽기/쓰기 불가 또는 극도로 느림)",
                "disk_usage_critical: 디스크 사용률이 임계치(90%+) 초과",
            ],
            "effect_observed": [
                {
                    "signal": "node_disk_pressure",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "노드의 DiskPressure condition이 True로 전환. kubectl describe node로 직접 확인",
                    "verification_hint": "kubectl_check: kubectl get node <node> -o jsonpath='{.status.conditions[?(@.type==\"DiskPressure\")].status}', expected=True",
                },
                {
                    "signal": "disk_write_test_fail",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "pod 내에서 디스크 쓰기 시도가 실패 (disk full 또는 I/O error). exec로 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<target> -n <ns> -c <container> -- sh -c 'dd if=/dev/zero of=/tmp/test bs=1M count=1 2>&1 || echo WRITE_FAILED', expected=No space left|WRITE_FAILED|I/O error",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "대상 EBS 볼륨 ID, EC2 인스턴스 ID, 또는 EFS 파일시스템 ID",
        "applicable_when": "서비스가 EBS 볼륨 또는 EFS를 사용하는 EC2/EKS 노드에서 실행될 때",
        "investigation_prompt": "{target_service} 서비스가 실행되는 호스트의 디스크 I/O 상태와 "
                                "스토리지 사용량을 점검하고, I/O 병목이 있는지 분석해주세요.",
        "detection_challenge": "디스크 I/O 지연은 앱 레벨에서 일반적인 타임아웃으로 나타남. "
                               "EBS VolumeReadOps/WriteOps, VolumeQueueLength 메트릭을 봐야 식별 가능. "
                               "디스크 가득 참은 로그 기록조차 실패하게 만듦.",
        "proactive_question": "VolumeQueueLength가 높거나 BurstBalance가 낮은 EBS 볼륨은? "
                              "디스크 사용률이 80% 이상인 인스턴스가 있는지 확인해주세요.",
        "restore_mechanism": [
            "FIS 실험 종료 (duration 기반 자동 복구)",
            "aws efs update-file-system --throughput-mode (원래 모드/값 복원)",
        ],
    },
    {
        "id": "FM-22",
        "name": "Graceful Degradation Test",
        "layer": "application",
        "description": "비핵심 의존 서비스를 순차적으로 차단(SG/NACL)하면서 핵심 기능이 유지되는지 검증. "
                       "Circuit breaker 동작, fallback 응답, 부분 서비스 가능 여부 등을 시뮬레이션. "
                       "다중 의존성을 순차/병렬로 차단하는 복합 시나리오.",
        "trigger_mode": "proactive",
        "trigger_mechanism": [
            "aws ec2 revoke-security-group-ingress (비핵심 의존성 순차 차단)",
            "aws lambda put-function-concurrency --reserved-concurrent-executions 0 (Lambda 의존성 완전 차단)",
            "aws elasticache reboot-cache-cluster + SG 차단 (캐시 + DB 동시 장애 조합)",
        ],
        "observation_signals": {
            "trigger_active": [
                "dependency_blocked: 비핵심 의존 서비스로의 연결이 차단됨",
                "circuit_breaker_open: Circuit breaker가 열려 fallback 경로로 전환됨",
            ],
            "effect_observed": [
                {
                    "signal": "dependency_connection_blocked",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "비핵심 의존성으로의 연결이 차단됨. exec로 연결 시도하여 직접 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- wget --timeout=3 -qO- http://<non-critical-dep>:<port>/ 2>&1, expected=timed out|Connection refused|FAILED",
                },
                {
                    "signal": "core_functionality_intact",
                    "effect_type": "infra_state",
                    "confidence": "high",
                    "description": "핵심 기능은 여전히 동작. 핵심 엔드포인트에 요청하여 정상 응답 확인",
                    "verification_hint": "kubectl_check: kubectl exec deploy/<caller> -n <ns> -c <container> -- wget --timeout=3 -qO- http://<core-svc>:<port>/<health> 2>&1, expected=200|OK|정상응답",
                },
            ],
            "reaction_confirmed": [
                "investigation_started: Agent가 조사 시작",
                "investigation_completed: Agent가 근본 원인 분석 완료",
            ],
        },
        "requires": "서비스의 의존성 그래프 (핵심 vs 비핵심 분류), 각 의존성의 SG ID",
        "applicable_when": "서비스가 2개 이상의 외부 의존성을 가지고 있을 때 (DB + 캐시, 또는 DB + 큐 + 외부 API 등)",
        "investigation_prompt": "{target_service} 서비스의 핵심 기능이 정상 동작하는지 점검하고, "
                                "일부 의존성 장애 시 서비스가 어떻게 degradation 되는지 분석해주세요.",
        "detection_challenge": "부분 장애 상태에서 핵심 기능은 동작하지만 비핵심 기능은 실패. "
                               "전체 서비스 건강 상태로는 정상으로 보이지만 특정 API 엔드포인트만 "
                               "에러를 반환하는 등 미묘한 차이를 식별해야 함.",
        "proactive_question": "서비스의 circuit breaker 또는 fallback 설정이 되어 있는 의존성은? "
                              "비핵심 의존성 장애 시에도 핵심 기능이 유지되는지 확인해주세요.",
        "restore_mechanism": [
            "aws ec2 authorize-security-group-ingress (차단된 SG 규칙 복원)",
            "aws lambda put-function-concurrency (원래 동시성 복원)",
        ],
    },
]


def get_failure_modes() -> list:
    return FAILURE_MODES


def get_failure_mode(fm_id: str) -> dict:
    for fm in FAILURE_MODES:
        if fm["id"] == fm_id:
            return fm
    return {}


# Legacy mapping: old ABSTRACT_TEMPLATE ID → new failure mode ID
_LEGACY_MAP = {
    "AWS-001": "FM-03",  # RDS Failover → Dependency Blackhole
    "AWS-002": "FM-01",  # VPC Endpoint → Network Isolation
    "AWS-003": "FM-05",  # IAM/STS → Permission Revoke
    "AWS-004": "FM-01",  # SG Block → Network Isolation
    "K8S-001": "FM-06",  # CoreDNS → DNS Disruption
    "K8S-002": "FM-02",  # Node Drain → Compute Kill
    "K8S-003": "FM-04",  # HPA Scaling → Resource Pressure
    "NET-001": "FM-04",  # Network Latency → Resource Pressure
    "NET-002": "FM-01",  # Packet Loss → Network Isolation
    "NET-003": "FM-01",  # Network Partition → Network Isolation
    "NET-004": "FM-10",  # HTTP Error → Endpoint Abuse
    "APP-001": "FM-04",  # OOM Kill → Resource Pressure
    "APP-002": "FM-04",  # CPU Stress → Resource Pressure
    "APP-003": "FM-02",  # Pod Failure → Compute Kill
    "APP-004": "FM-04",  # Disk I/O → Resource Pressure
    "APP-005": "FM-08",  # ConfigMap → Config Tamper
    "CMP-001": "FM-03",  # Cascading → Dependency Blackhole
    "CMP-002": "FM-10",  # Thundering Herd → Endpoint Abuse
}


def map_legacy_id(old_id: str) -> str:
    return _LEGACY_MAP.get(old_id, "")
