# Investigation DAG (조사 저널 분석)

## 개요

DAG(Directed Acyclic Graph)는 DevOps Agent의 장애 조사 과정을 **가설 기반 그래프**로 재구성하여 시각화한다.
개별 조사 단계가 아닌, 경쟁하는 가설들과 그 확인/기각 과정을 보여준다.

---

## 1. 데이터 흐름

```
DevOps Agent 조사 실행
  → journal_records (시간순 이벤트)
  → Bedrock Claude 분석 (가설 DAG 재구성)
  → 시각화 (hypothesis graph + causal chain)
```

---

## 2. Raw Journal (`/api/investigation-journal-raw`)

Agent Space의 조사 이벤트를 시간순으로 반환:

| record_type | 의미 |
|-------------|------|
| symptom | 초기 증상 감지 |
| observation | 관측 데이터 수집 |
| finding | 발견/결론 |
| investigation_summary | 조사 요약 |

---

## 3. AI 분석 (`/api/investigation-journal`)

### 입력

- 전체 execution의 journal records (최대 10개/task)
- Bedrock Claude에게 가설 DAG 재구성 프롬프트 전송

### 출력 구조

```json
{
  "hypotheses": [
    {
      "id": "short-kebab-id",
      "label": "한국어 근본 원인 이론",
      "status": "confirmed | rejected | partial",
      "steps": [
        {
          "signal_type": "metric | trace | log | code_snippet | change_event",
          "obs_id": "관측 ID",
          "insight": "이 신호가 의미하는 것",
          "is_key": true
        }
      ]
    }
  ],
  "causal_chain": [
    {"step": 1, "service": "서비스명", "event": "발생한 일"}
  ],
  "root_cause": {
    "title": "근본 원인",
    "description": "상세 설명"
  },
  "evaluation": {
    "root_cause_match": {"score": 0-100},
    "causal_chain": {"score": 0-100},
    "data_sources": {"score": 0-100},
    "false_leads": {"score": 0-100}
  }
}
```

### 핵심: 가설 중심 재구성

- 개별 step이 아닌 **경쟁하는 이론(hypotheses)**으로 그룹화
- 각 가설은 confirmed/rejected/partial 상태
- 관측 데이터를 가설 증거로 매핑
- 인과 체인: root_cause → cascades_to → alarm termination

---

## 4. DAG 검증 (`/api/dag-verify`)

### 검증 항목

| 항목 | 설명 |
|------|------|
| Orphan observations | 어떤 finding에도 연결되지 않은 관측 |
| Bad edges | 존재하지 않는 노드를 참조하는 엣지 |
| Empty observations | target/evidence가 없는 관측 |
| Duplicate findings | 같은 ID의 중복 findings (상위 rank 유지) |

### Chain Tracing

- root_cause → cascades_to 엣지를 따라 종단까지 추적
- 끊어진 체인 감지
- Activity group으로 그룹화

### 출력

- Activity groups (연결된 노드 클러스터)
- Chain topology (방향성 경로)
- Node/edge counts

---

## 5. UI 시각화

템플릿: `dag.html`

- 가설 노드: 색상으로 상태 표시 (confirmed=green, rejected=red, partial=yellow)
- 관측 노드: signal_type 아이콘
- 엣지: 증거 관계 + 인과 관계
- Causal chain: 하단 타임라인으로 별도 표시

---

## 6. 평가 스코어링

| 차원 | 측정 |
|------|------|
| root_cause_match | 실제 주입한 장애와 Agent 결론 일치도 |
| causal_chain | 인과 관계 정확성 |
| data_sources | 다양한 신호 활용도 (metric/trace/log) |
| false_leads | 잘못된 가설 기각 능력 |

---

## 7. 주요 함수 참조

| 함수 | 파일 | 역할 |
|------|------|------|
| api_investigation_journal | routes_dag.py | AI DAG 분석 |
| api_investigation_journal_raw | routes_dag.py | Raw journal 반환 |
| api_dag_verify | routes_dag.py | DAG 구조 검증 |

---

## 8. 설계 원칙

1. **가설 중심**: 단계 나열이 아닌 경쟁 이론 구조 — Agent의 추론 품질을 평가 가능
2. **AI 재구성**: Raw 이벤트를 Claude가 구조화 → 사람이 읽기 쉬운 형태
3. **검증 가능**: DAG 구조 자체의 무결성 검증 (orphan, bad edges)
4. **점수화**: 4차원 평가로 Agent 조사 품질 정량 측정
