---
name: k8s-detail
description: >
  K8s 리소스 상세 수집 요청 시 적용. 사용자가 #k8s-detail 트리거를 보내면
  특정 앱이 사용하는 namespace 단위 전체 K8s 리소스를 조사하여
  표준 JSON 포맷으로 응답한다. 워크로드, ServiceAccount, Secret, ConfigMap,
  PVC, NetworkPolicy, Ingress를 포함한 완전한 K8s 인벤토리를 제공한다.
agent_types:
  - Generic
---

# k8s-detail — K8s 리소스 상세 수집 스킬

앱에서 `#k8s-detail` 트리거를 보내면, 이 스킬의 포맷과 규칙에 따라 K8s 리소스를 조사하고 응답합니다.

---

## 트리거

- `#k8s-detail {app_name}` — 특정 앱의 K8s 리소스 상세 수집

---

## 역할
K8s 인프라 전문가. 앱이 사용하는 모든 namespace의 K8s 리소스를 빠짐없이 조사하여 구조화된 JSON으로 제공합니다.

---

## 조사 항목 (8개 카테고리)

### 1. Namespace 정보
- 앱 관련 모든 namespace 이름, labels
- ResourceQuota, LimitRange 설정 여부

### 2. 워크로드 (Deployment, StatefulSet, DaemonSet, CronJob, Job)
각 워크로드에 대해:
- kind, namespace, replicas
- containers: name, image, resource requests/limits, ports, liveness/readiness probes
- init containers 목록
- 환경변수 참조: 어떤 ConfigMap, Secret에서 가져오는지 (env_from)
- 연결된 Service: type (ClusterIP/LoadBalancer/NodePort), ports, loadBalancer hostname
- HPA: min/max replicas, metrics
- PDB: minAvailable 또는 maxUnavailable
- volumes: PVC, ConfigMap, Secret, emptyDir 등
- annotations (특히 instrumentation, sidecar injection 관련)
- nodeSelector, tolerations

### 3. ServiceAccount
- 각 namespace의 ServiceAccount 목록 (default 제외)
- IRSA annotation (eks.amazonaws.com/role-arn) 값
- 연결된 Secret 목록

### 4. Secret
- 각 namespace의 Secret 목록 (default-token, helm 관련 제외)
- type (Opaque, kubernetes.io/tls 등)
- key 목록만 (값은 절대 포함 금지)

### 5. ConfigMap
- 각 namespace의 ConfigMap 목록 (kube-root-ca 등 시스템 제외)
- key 목록

### 6. PersistentVolumeClaim
- name, namespace, storageClass, capacity, accessMode, status

### 7. NetworkPolicy
- 존재 여부, 규칙 요약

### 8. Ingress
- name, namespace, rules (hosts, paths, backend), TLS 설정

---

## 출력 JSON 포맷

```json 코드블록 안에 작성할 것:

```json
{
  "app_name": "{app_name}",
  "namespaces": [
    {
      "name": "namespace-name",
      "labels": {},
      "resource_quota": null,
      "limit_range": null
    }
  ],
  "workloads": [
    {
      "name": "이전 Q2에서 식별된 노드명과 동일",
      "kind": "Deployment",
      "namespace": "실제 K8s namespace",
      "replicas": 1,
      "containers": [
        {
          "name": "container-name",
          "image": "registry/image:tag",
          "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}},
          "ports": [80],
          "probes": {"liveness": {"httpGet": "/", "port": 80}, "readiness": {"httpGet": "/", "port": 80}},
          "env_from": ["configmap/config-name", "secret/secret-name"]
        }
      ],
      "init_containers": [],
      "service_account": "sa-name",
      "hpa": null,
      "pdb": null,
      "service": {"type": "ClusterIP", "ports": [{"port": 80, "targetPort": 80}], "loadBalancer": null},
      "volumes": [{"name": "data", "type": "PVC", "claim": "data-pvc"}],
      "annotations": {},
      "node_selector": {},
      "tolerations": []
    }
  ],
  "service_accounts": [
    {"name": "sa-name", "namespace": "ns", "irsa_role_arn": "arn:aws:iam::role/...", "secrets": []}
  ],
  "secrets": [
    {"name": "secret-name", "namespace": "ns", "type": "Opaque", "keys": ["key1", "key2"]}
  ],
  "configmaps": [
    {"name": "cm-name", "namespace": "ns", "keys": ["KEY1", "KEY2"]}
  ],
  "persistent_volume_claims": [
    {"name": "pvc-name", "namespace": "ns", "storage_class": "gp2", "capacity": "1Gi", "access_mode": "ReadWriteOnce", "status": "Bound"}
  ],
  "network_policies": [
    {"name": "policy-name", "namespace": "ns", "description": "설명 (한국어)"}
  ],
  "ingresses": [
    {"name": "ing-name", "namespace": "ns", "rules": [{"host": "...", "paths": [{"path": "/", "backend": "svc:80"}]}], "tls": false}
  ]
}
```

---

## 규칙

1. workloads[].name은 이전 Q2에서 식별한 노드 이름과 **정확히 일치**
2. kind는 정확한 K8s 리소스 타입만: Deployment, StatefulSet, DaemonSet, CronJob, Job
3. hpa, pdb는 없으면 **null**
4. 빈 목록은 빈 배열 **[]**
5. Secret **값**은 절대 포함 금지 — key 이름만
6. default ServiceAccount, kube-system의 시스템 리소스는 **제외**
7. 모든 description은 **한국어**로 작성
8. ```` ```json ```` 코드블록 안에 JSON 작성

---

## 앱에서 트리거 시 함께 보내는 동적 데이터

앱은 트리거 키워드와 함께 아래 동적 데이터를 제공합니다:
- 앱 이름
- 이전 Q2에서 식별된 서비스 목록 (워크로드명, namespace)
- EKS 클러스터 이름

이 동적 데이터를 활용하여 해당 앱의 K8s 리소스를 상세히 조사하세요.

---

## 응답 형식

1. 조사 결과 간단 설명 (발견한 리소스 요약)
2. ```` ```json ```` 코드블록 안에 위 포맷의 JSON
