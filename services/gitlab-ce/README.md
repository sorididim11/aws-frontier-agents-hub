# GitLab CE (Private Test Instance)

EKS에 배포하는 Private GitLab CE 인스턴스. DevOps Agent의 데이터소스로 연결하여 코드 분석에 활용.

## 배포

```bash
kubectl apply -k services/gitlab-ce/k8s/
```

## 구성

| 항목 | 값 |
|------|-----|
| Image | `gitlab/gitlab-ce:17.6.0-ce.0` |
| External URL | `http://gitlab.internal` |
| 리소스 | 500m~2 CPU, 4~6Gi Memory |
| Ingress | Internal ALB (HTTPS 443 + HTTP 80) |
| Root Password | deployment.yml 내 `GITLAB_OMNIBUS_CONFIG` 참조 |

## 참고

- OTEL auto-instrumentation 비활성화 (annotations)
- Prometheus monitoring 비활성화 (메모리 절약)
- Puma worker 0 (단일 프로세스 모드, 테스트용)
- Startup probe failureThreshold=80 (GitLab 초기 기동 ~20분 허용)
