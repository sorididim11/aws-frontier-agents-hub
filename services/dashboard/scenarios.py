# DevOps Agent Test Scenarios Metadata
SCENARIOS = {
    "application": {
        "name": "Application Layer",
        "icon": "🔥",
        "scenarios": [
            {
                "id": "oom",
                "name": "OOMKilled",
                "trigger": "1",
                "purpose": "메모리 한계 초과 시 Pod 재시작 감지",
                "flow": [
                    "hasher /oom 엔드포인트 호출",
                    "메모리 128Mi 초과 할당",
                    "커널 OOM Killer 동작",
                    "Pod 재시작 (Exit Code 137)"
                ],
                "implementation": "hasher.rb에서 대용량 배열 생성으로 메모리 소진",
                "agent_expected": "Resource Limits - 메모리 limit 증가 권장",
                "command": "kubectl exec -n dockercoins deployment/worker -- wget -q -O- http://hasher/oom --timeout=60 || echo 'OOM triggered (expected timeout)'"
            },
            {
                "id": "latency",
                "name": "High Latency",
                "trigger": "4",
                "purpose": "응답 지연 감지 및 병목 식별",
                "flow": [
                    "hasher /slow?delay=5 호출",
                    "5초 sleep 후 응답",
                    "CloudWatch Latency 메트릭 증가",
                    "알람 트리거"
                ],
                "implementation": "hasher.rb에서 sleep(delay) 추가",
                "agent_expected": "Performance Issue - 병목 지점 식별",
                "command": "kubectl exec -n dockercoins deployment/worker -- wget -q -O- 'http://hasher/slow?delay=5' --timeout=10"
            },
            {
                "id": "error",
                "name": "HTTP 500 Errors",
                "trigger": "7",
                "purpose": "서버 에러 감지 및 로그 분석",
                "flow": [
                    "hasher /error 엔드포인트 호출",
                    "HTTP 500 응답 반환",
                    "CloudWatch Error/Fault 메트릭 증가"
                ],
                "implementation": "hasher.rb에서 의도적 500 에러 반환",
                "agent_expected": "Application Error - 에러 로그 분석",
                "command": "kubectl exec -n dockercoins deployment/worker -- wget -q -O- http://hasher/error --timeout=10 || echo 'HTTP 500 triggered'"
            },
            {
                "id": "cpu",
                "name": "CPU Spike",
                "trigger": "8",
                "purpose": "CPU 과부하 감지",
                "flow": [
                    "rng /cpu?duration=60 호출",
                    "60초간 busy loop 실행",
                    "CPU 사용률 급증",
                    "다른 Pod 영향 가능"
                ],
                "implementation": "rng.py에서 busy loop으로 CPU 100% 사용",
                "agent_expected": "Resource Issue - CPU throttling 확인",
                "command": "kubectl exec -n dockercoins deployment/worker -- wget -q -O- 'http://rng/cpu?duration=30' --timeout=40 &"
            },
            {
                "id": "crash",
                "name": "Process Crash",
                "trigger": "crash",
                "purpose": "프로세스 크래시 감지",
                "flow": [
                    "hasher /crash 엔드포인트 호출",
                    "프로세스 강제 종료 (exit 1)",
                    "Pod 재시작"
                ],
                "implementation": "hasher.rb에서 Kernel.exit(1) 호출",
                "agent_expected": "Application Error - 크래시 원인 분석",
                "command": "kubectl exec -n dockercoins deployment/worker -- wget -q -O- http://hasher/crash --timeout=10 || echo 'Crash triggered'"
            }
        ]
    },
    "kubernetes": {
        "name": "Kubernetes Platform Layer",
        "icon": "☸️",
        "scenarios": [
            {
                "id": "imagepull",
                "name": "ImagePullBackOff",
                "trigger": "2",
                "purpose": "잘못된 이미지 태그로 인한 배포 실패 감지",
                "flow": [
                    "존재하지 않는 이미지 태그로 Deployment 생성",
                    "kubelet이 이미지 풀 시도",
                    "ECR에서 404 반환",
                    "Pod ImagePullBackOff 상태"
                ],
                "implementation": "test-imagepull-fail Deployment with nonexistent-v999 tag",
                "agent_expected": "Deployment Issue - 올바른 이미지 태그 확인",
                "command": "kubectl apply -f - <<EOF\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test-imagepull-fail\n  namespace: dockercoins\nspec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: test-imagepull-fail\n  template:\n    metadata:\n      labels:\n        app: test-imagepull-fail\n    spec:\n      containers:\n      - name: test\n        image: ${ECR_REGISTRY}/${PROJECT_NAME}/hasher:nonexistent-v999\nEOF"
            },
            {
                "id": "crashloop",
                "name": "CrashLoopBackOff",
                "trigger": "3",
                "purpose": "앱 시작 실패로 인한 반복 재시작 감지",
                "flow": [
                    "시작 시 즉시 exit 1하는 컨테이너 배포",
                    "kubelet이 컨테이너 재시작 시도",
                    "반복 실패로 CrashLoopBackOff",
                    "백오프 시간 증가 (10s, 20s, 40s...)"
                ],
                "implementation": "busybox 컨테이너에서 에러 메시지 출력 후 exit 1",
                "agent_expected": "Application Error - 로그 확인, 설정 검증",
                "command": "kubectl apply -f - <<EOF\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test-crashloop\n  namespace: dockercoins\nspec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: test-crashloop\n  template:\n    metadata:\n      labels:\n        app: test-crashloop\n    spec:\n      containers:\n      - name: test\n        image: busybox:latest\n        command: [\"sh\", \"-c\", \"echo 'ERROR: Missing required configuration' && exit 1\"]\nEOF"
            },
            {
                "id": "networkpolicy",
                "name": "NetworkPolicy Block",
                "trigger": "10",
                "purpose": "네트워크 정책으로 인한 통신 차단 감지",
                "flow": [
                    "모든 ingress 차단하는 NetworkPolicy 적용",
                    "Pod 간 통신 시도",
                    "연결 타임아웃",
                    "서비스 접근 불가"
                ],
                "implementation": "NetworkPolicy로 특정 라벨 Pod의 모든 ingress 차단",
                "agent_expected": "Network Issue - NetworkPolicy 확인",
                "command": "kubectl apply -f - <<EOF\napiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: test-deny-all\n  namespace: dockercoins\nspec:\n  podSelector:\n    matchLabels:\n      network-test: blocked\n  policyTypes:\n  - Ingress\n  ingress: []\nEOF"
            },
            {
                "id": "configmap",
                "name": "ConfigMap Missing",
                "trigger": "13",
                "purpose": "존재하지 않는 ConfigMap 참조 감지",
                "flow": [
                    "없는 ConfigMap 참조하는 Pod 생성",
                    "kubelet이 ConfigMap 조회 실패",
                    "CreateContainerConfigError 상태"
                ],
                "implementation": "nonexistent-configmap 참조하는 환경변수 설정",
                "agent_expected": "Configuration Issue - ConfigMap 생성 필요",
                "command": "kubectl apply -f - <<EOF\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test-configmap-missing\n  namespace: dockercoins\nspec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: test-configmap-missing\n  template:\n    metadata:\n      labels:\n        app: test-configmap-missing\n    spec:\n      containers:\n      - name: app\n        image: busybox:latest\n        command: [\"sleep\", \"3600\"]\n        env:\n        - name: APP_CONFIG\n          valueFrom:\n            configMapKeyRef:\n              name: nonexistent-configmap\n              key: config\nEOF"
            },
            {
                "id": "liveness",
                "name": "Liveness Probe Failure",
                "trigger": "15",
                "purpose": "활성 프로브 실패로 인한 Pod 재시작 감지",
                "flow": [
                    "실패하는 liveness probe 설정된 Pod 배포",
                    "kubelet이 /healthz 호출",
                    "연속 3회 실패",
                    "Pod 재시작"
                ],
                "implementation": "존재하지 않는 /healthz 엔드포인트로 HTTP probe 설정",
                "agent_expected": "Health Check Issue - probe 설정 확인",
                "command": "kubectl apply -f - <<EOF\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test-liveness-fail\n  namespace: dockercoins\nspec:\n  replicas: 1\n  selector:\n    matchLabels:\n      app: test-liveness-fail\n  template:\n    metadata:\n      labels:\n        app: test-liveness-fail\n    spec:\n      containers:\n      - name: app\n        image: busybox:latest\n        command: [\"sleep\", \"3600\"]\n        livenessProbe:\n          httpGet:\n            path: /healthz\n            port: 8080\n          initialDelaySeconds: 5\n          periodSeconds: 5\nEOF"
            }
        ]
    },
    "aws": {
        "name": "AWS Infrastructure Layer",
        "icon": "☁️",
        "scenarios": [
            {
                "id": "sg-block",
                "name": "Security Group Block",
                "trigger": "20",
                "purpose": "보안 그룹 규칙 제거로 인한 DB 연결 실패 감지",
                "flow": [
                    "RDS Security Group에서 5432 포트 규칙 제거",
                    "EKS → RDS 연결 시도",
                    "연결 타임아웃 (30초)",
                    "앱 에러 발생"
                ],
                "implementation": "aws ec2 revoke-security-group-ingress로 규칙 제거",
                "agent_expected": "Network Issue - Security Group 규칙 확인",
                "command": "# Security Group 규칙 제거 (RDS 접근 차단)\nSG_ID=$(aws ec2 describe-security-groups --filters 'Name=group-name,Values=${PROJECT_NAME}-db-sg' --query 'SecurityGroups[0].GroupId' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws ec2 revoke-security-group-ingress --group-id $SG_ID --protocol tcp --port 5432 --cidr 10.0.11.0/24 --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            },
            {
                "id": "sg-restore",
                "name": "Security Group Restore",
                "trigger": "21",
                "purpose": "Security Group 규칙 복원",
                "flow": [
                    "RDS Security Group에 5432 포트 규칙 추가",
                    "EKS → RDS 연결 복구"
                ],
                "implementation": "aws ec2 authorize-security-group-ingress로 규칙 추가",
                "agent_expected": "N/A - 복구 작업",
                "command": "# Security Group 규칙 복원\nSG_ID=$(aws ec2 describe-security-groups --filters 'Name=group-name,Values=${PROJECT_NAME}-db-sg' --query 'SecurityGroups[0].GroupId' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 5432 --cidr 10.0.11.0/24 --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            },
            {
                "id": "fis-node-terminate",
                "name": "FIS Node Termination",
                "trigger": "41",
                "purpose": "EKS 노드 종료로 인한 Pod 재스케줄링 감지",
                "flow": [
                    "FIS 실험 시작",
                    "EKS 노드 1개 종료",
                    "해당 노드의 Pod들 Terminating",
                    "다른 노드로 재스케줄링"
                ],
                "implementation": "AWS FIS aws:ec2:terminate-instances 액션",
                "agent_expected": "Infrastructure Issue - 노드 장애 감지, Pod 재배치 확인",
                "command": "# FIS 노드 종료 실험 시작\nTEMPLATE_ID=$(aws fis list-experiment-templates --query 'experimentTemplates[?tags.Scenario==`I07-node-failure`].id' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws fis start-experiment --experiment-template-id $TEMPLATE_ID --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            },
            {
                "id": "fis-rds-reboot",
                "name": "FIS RDS Reboot",
                "trigger": "42",
                "purpose": "RDS 재시작으로 인한 연결 끊김 감지",
                "flow": [
                    "FIS 실험 시작",
                    "RDS 인스턴스 재시작",
                    "기존 DB 연결 끊김",
                    "앱 에러 발생 → 복구"
                ],
                "implementation": "AWS FIS aws:rds:reboot-db-instances 액션",
                "agent_expected": "Database Issue - RDS 재시작 감지, 연결 재시도 권장",
                "command": "# FIS RDS 재시작 실험 시작\nTEMPLATE_ID=$(aws fis list-experiment-templates --query 'experimentTemplates[?tags.Scenario==`I08-rds-failover`].id' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws fis start-experiment --experiment-template-id $TEMPLATE_ID --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            }
        ]
    },
    "composite": {
        "name": "Composite Scenarios (복합)",
        "icon": "🔗",
        "scenarios": [
            {
                "id": "c01-redis",
                "name": "C01: Redis 장애 전파",
                "trigger": "50",
                "purpose": "캐시/큐 서버 장애가 전체 시스템에 미치는 영향 분석",
                "flow": [
                    "redis Pod 삭제 (근본 원인)",
                    "worker: ConnectionError to redis:6379",
                    "worker 재시도 루프 (10초 대기)",
                    "처리량 0 → webui 표시 중단"
                ],
                "implementation": "kubectl delete pod -l app=redis",
                "agent_expected": "Root Cause: redis Pod 장애 (NOT worker 코드 버그)",
                "command": "kubectl delete pod -n dockercoins -l app=redis --force --grace-period=0"
            },
            {
                "id": "c02-network",
                "name": "C02: 네트워크 차단 연쇄",
                "trigger": "51",
                "purpose": "Security Group 변경이 앱에 미치는 연쇄 영향 분석",
                "flow": [
                    "SG에서 5432 포트 규칙 제거 (근본 원인)",
                    "rng: DB 연결 타임아웃 (30초)",
                    "rng: HTTP 500 반환",
                    "worker 영향 (rng 호출 실패)"
                ],
                "implementation": "aws ec2 revoke-security-group-ingress",
                "agent_expected": "Root Cause: Security Group 변경 (NOT DB 서버 장애)",
                "command": "# SG 규칙 제거 → DB 타임아웃 → 앱 에러\nSG_ID=$(aws ec2 describe-security-groups --filters 'Name=group-name,Values=${PROJECT_NAME}-db-sg' --query 'SecurityGroups[0].GroupId' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws ec2 revoke-security-group-ingress --group-id $SG_ID --protocol tcp --port 5432 --cidr 10.0.11.0/24 --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            },
            {
                "id": "c03-node",
                "name": "C03: 노드 장애 연쇄",
                "trigger": "52",
                "purpose": "노드 장애가 서비스에 미치는 영향 분석",
                "flow": [
                    "FIS로 노드 1개 종료 (근본 원인)",
                    "해당 노드의 Pod들 Terminating",
                    "Pod 재스케줄링 (30초~1분)",
                    "일시적 서비스 불안정"
                ],
                "implementation": "AWS FIS aws:ec2:terminate-instances",
                "agent_expected": "Root Cause: EKS 노드 장애 (NOT Pod 자체 문제)",
                "command": "# FIS 노드 종료 실험\nTEMPLATE_ID=$(aws fis list-experiment-templates --query 'experimentTemplates[?tags.Scenario==`I07-node-failure`].id' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws fis start-experiment --experiment-template-id $TEMPLATE_ID --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            },
            {
                "id": "c04-rds",
                "name": "C04: RDS 페일오버 연쇄",
                "trigger": "53",
                "purpose": "DB 재시작이 앱에 미치는 영향 분석",
                "flow": [
                    "FIS로 RDS 재시작 (근본 원인)",
                    "기존 DB 연결 끊김",
                    "rng: SSL connection closed unexpectedly",
                    "연결 재시도 또는 Pod 재시작 필요"
                ],
                "implementation": "AWS FIS aws:rds:reboot-db-instances",
                "agent_expected": "Root Cause: RDS 재시작/페일오버 (NOT 앱 코드 버그)",
                "command": "# FIS RDS 재시작 실험\nTEMPLATE_ID=$(aws fis list-experiment-templates --query 'experimentTemplates[?tags.Scenario==`I08-rds-failover`].id' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws fis start-experiment --experiment-template-id $TEMPLATE_ID --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            },
            {
                "id": "c05-service",
                "name": "C05: 서비스 의존성 장애",
                "trigger": "54",
                "purpose": "마이크로서비스 의존성 장애 분석",
                "flow": [
                    "hasher replicas=0 (근본 원인)",
                    "worker: ConnectionError to hasher",
                    "worker 재시도 루프",
                    "처리량 저하"
                ],
                "implementation": "kubectl scale deployment hasher --replicas=0",
                "agent_expected": "Root Cause: hasher 서비스 다운 (NOT worker 코드 버그)",
                "command": "kubectl scale deployment hasher -n dockercoins --replicas=0"
            },
            {
                "id": "c06-resource",
                "name": "C06: 리소스 경쟁 연쇄",
                "trigger": "55",
                "purpose": "노드 리소스 부족이 앱에 미치는 영향 분석",
                "flow": [
                    "FIS로 노드 CPU 80% 스트레스 (근본 원인)",
                    "모든 Pod CPU throttling",
                    "응답 시간 증가",
                    "클라이언트 타임아웃/에러"
                ],
                "implementation": "AWS FIS aws:ssm:send-command with stress-ng",
                "agent_expected": "Root Cause: 노드 CPU 리소스 부족 (NOT 앱 코드 성능 문제)",
                "command": "# FIS CPU 스트레스 실험\nTEMPLATE_ID=$(aws fis list-experiment-templates --query 'experimentTemplates[?tags.Scenario==`I11-node-cpu-stress`].id' --output text --region ${AWS_REGION:-us-east-1} --no-cli-pager)\naws fis start-experiment --experiment-template-id $TEMPLATE_ID --region ${AWS_REGION:-us-east-1} --no-cli-pager"
            }
        ]
    },
    "cleanup": {
        "name": "Cleanup & Restore",
        "icon": "🧹",
        "scenarios": [
            {
                "id": "cleanup",
                "name": "테스트 리소스 정리",
                "trigger": "cleanup",
                "purpose": "모든 테스트 Deployment/Pod 삭제",
                "flow": ["테스트용 Deployment 삭제", "테스트용 Pod 삭제", "ResourceQuota 삭제"],
                "implementation": "kubectl delete로 test-* 리소스 삭제",
                "agent_expected": "N/A",
                "command": "kubectl delete deployment -n dockercoins -l app=test-imagepull-fail,app=test-crashloop,app=test-configmap-missing,app=test-liveness-fail --ignore-not-found"
            },
            {
                "id": "restore-hasher",
                "name": "Hasher 복구",
                "trigger": "restore-hasher",
                "purpose": "hasher replicas=1로 복구",
                "flow": ["hasher Deployment replicas=1 설정"],
                "implementation": "kubectl scale",
                "agent_expected": "N/A",
                "command": "kubectl scale deployment hasher -n dockercoins --replicas=1"
            },
            {
                "id": "status",
                "name": "현재 상태 확인",
                "trigger": "status",
                "purpose": "Pod 상태 및 테스트 리소스 확인",
                "flow": ["kubectl get pods", "테스트 리소스 목록"],
                "implementation": "kubectl get",
                "agent_expected": "N/A",
                "command": "kubectl get pods -n dockercoins -o wide"
            }
        ]
    }
}
