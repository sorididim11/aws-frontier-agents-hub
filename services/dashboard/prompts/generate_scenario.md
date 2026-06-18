당신은 카오스 엔지니어링 전문가입니다. 아키텍처 분석 기반 추천 시나리오를 **실행 가능한 시뮬레이션 시나리오 JSON**으로 변환하세요.

**중요**: 플랫폼에 종속되지 않는 시나리오를 생성하세요. 트리거는 AWS CLI와 FIS만 사용합니다.

**절대 금지 — 런타임 리소스 ID 하드코딩:**
시나리오는 반복 실행됩니다. 현재 시점의 리소스 ID를 넣으면 다음 실행에서 깨집니다.
- PodName (pod 재배포 시 변경), InstanceId (노드 교체 시 변경) 절대 사용 금지
- metric_check dimensions은 ClusterName, Namespace 같은 안정적 식별자만 사용
- architecture에 pod명, instance-id 등 런타임 값 포함 금지

**Pod lifecycle 규칙:**
- kubectl run 일회성 pod (load generator 등)는 완료 후 Succeeded 상태 (Running이 아님)
- kubectl_check expected="Running"은 Deployment 상시 서비스에만. 일회성 작업은 expected="Succeeded"

## 추천 정보
- 장애 모드: {template_id} — {template_name}
- 이름: {rec_name}
- 대상: {target_json}
- 트리거 모드: {trigger_mode}
- 근거: {rationale}
- 예상 영향: {expected_impact}
- 조사 프롬프트: {investigation_prompt}

## 아키텍처 그래프 (실제 서비스 및 통신 경로)
{architecture_json}

## 환경 변수 (플레이스홀더 사용 필수)
- ${{PROJECT_NAME}} — 프로젝트 이름
- ${{AWS_ACCOUNT_ID}} — AWS 계정 ID
- ${{AWS_REGION}} — AWS 리전

## 가용 CloudWatch 알람 (full spec 포함)
각 알람에 metric, dimensions, threshold, period, eval_periods가 포함됩니다.
- dimensions에 ClusterName만 있으면 **클러스터 전체 노드 평균** → 전체 노드에 영향을 주는 주입만 가능
- dimensions에 NodeName이 있으면 **개별 노드** → 단일 노드 주입 가능
- dimensions에 Service가 있으면 **ApplicationSignals 앱 레벨** → FIS가 아닌 inject-latency 방식 필요
- threshold와 period×eval_periods를 보고, **FIS 주입 방식으로 해당 threshold를 실제로 초과할 수 있는지** 산술적으로 판단하세요
- 물리적으로 불가능한 조합은 생성하지 마세요 (예: 2노드 클러스터에서 COUNT(1) FIS로 클러스터 평균 80% 초과 불가)

{alarms_json}

## 가용 FIS 실험 템플릿 (멀티어카운트)
각 템플릿에 account_id, profile, id, tags가 포함됩니다.

**절대 규칙 — FIS 템플릿 사용:**
- trigger.command에 **아래 목록의 ID를 직접 사용** (예: `aws fis start-experiment --experiment-template-id EXTAdZRLLzQhZ9vzc`)
- 동적 태그 검색 쿼리 (`$(aws fis list-experiment-templates --query ...)`) **절대 금지** — ID가 이미 제공됨
- 목록에 없는 템플릿 ID나 태그명을 추측/발명하지 마세요
- 해당 템플릿의 계정 profile이 자동 주입되므로 --profile 불필요
- FIS 시나리오의 restore는 `aws fis stop-experiment --id ${{FIS_EXPERIMENT_ID}}`
- 새 템플릿을 create-experiment-template으로 생성하지 마세요 (IaC 원칙 위반)
- 적합한 FIS 템플릿이 목록에 없으면, trigger.type을 **aws_cli**로 변경하고 직접 명령어를 작성하세요

{fis_templates_json}

## 검증 단계 타입 스키마
{step_types_json}

## 참고 시나리오 (few-shot exemplar)
{exemplars_json}

## 생성 규칙
1. **한국어**로 모든 텍스트 작성 (purpose, flow, rubric 등)
2. trigger.command에 **플레이스홀더** 사용 (${{PROJECT_NAME}} 등)
3. trigger.type은 **aws_cli 또는 fis** 사용 (앱 내부 endpoint 접근 불가). fis 사용 시 반드시 아래 템플릿 목록의 ID를 직접 기재
4. **restore 명령어 필수** — 장애 주입 후 원상복구
5. evaluation_rubric의 **weight 합계 = 100**
6. verification.steps에 최소 3단계 포함
7. 시나리오 ID: "{scenario_id}"
8. `"source": "ai-generated"` 메타데이터 추가
9. pre_cleanup으로 이전 상태 정리 (reset_alarms 포함)
10. normal_flow와 fault_flow 모두 작성
11. **trigger_mode 필수** — reactive(알람 기반) 또는 proactive(Agent 질문 기반)
12. proactive 시나리오: investigation_prompt, observation_window(초) 필드 포함
13. trigger.command는 **단일 문자열** (commands 배열 금지, 여러 명령은 && 연결)
14. 환경 변수: 글로벌(${{PROJECT_NAME}}, ${{AWS_ACCOUNT_ID}}, ${{AWS_REGION}}, ${{FIS_EXPERIMENT_ID}}) + 시나리오 `variables` 필드에 선언된 변수만 허용. 미선언 변수 **절대 금지**. variables 예시: {{"HOSTED_ZONE_ID": {{"discovery": "aws route53 list-hosted-zones --query 'HostedZones[?Name==`...`].Id' --output text"}}}}. ${{FIS_EXPERIMENT_ID}}는 trigger 출력에서 자동 추출됨
15. trigger가 생성하는 리소스(pod, deployment 등) 이름 = verification.steps에서 참조하는 이름 (**일치 필수**)
16. kubectl run으로 pod 생성 시, verification의 kubectl_check에서 **동일한 pod 이름** 사용
17. metric_check dimensions 허용 목록: **Service, Operation, Environment, Namespace** 만 사용. PodName, InstanceId, NodeName 등 변동성 dimension **절대 금지** (pod/instance는 재배포 시 변경되므로 시나리오가 깨짐)

JSON만 응답하세요 (마크다운 코드블록 안에):
```json
{{
  "id": "{scenario_id}",
  "source": "ai-generated",
  "failure_mode_id": "{template_id}",
  "trigger_mode": "reactive|proactive",
  "name": "...",
  "category": "infrastructure|application|composite",
  "layer": "...",
  "purpose": "...",
  "architecture": {{...}},
  "normal_flow": [...],
  "fault_flow": [...],
  "investigation_goal": "...",
  "expected_root_cause": "...",
  "investigation_prompt": "proactive인 경우 Agent에게 보낼 조사 질문",
  "observation_window": 120,
  "variables": {{"VAR_NAME": {{"discovery": "셸 명령어로 값 조회"}}}},
  "trigger": {{"type": "aws_cli|fis", "command": "..."}},
  "pre_cleanup": {{"command": "...", "reset_alarms": [...], "wait_ok_timeout": 60}},
  "restore": {{"command": "..."}},
  "verification": {{"steps": [{{"name": "검증 이름(한국어)", "type": "step_type", ...타입별 필드}}]}},
  "evaluation_rubric": {{...}}
}}
```