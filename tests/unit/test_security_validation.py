"""
PoC: Security Finding → 방어 검증 자동화

가설: Security Agent attackScript를 파싱 → 재실행 → 모니터링 감지 여부 검증

테스트 시나리오:
1. HIGH finding "500 Error Info Disclosure" → GET /json → stack trace 노출 확인
   - 검증: 에러 응답에 내부 경로가 포함되는가? (취약)
   - 검증: CloudWatch에 5xx 알람이 있는가? (모니터링 동작 확인)

2. HIGH finding "X-Powered-By" → HEAD / → 헤더 노출 확인
   - 검증: X-Powered-By 헤더가 존재하는가? (취약)

3. FALSE_POSITIVE "DoS /crash" → 내부 서비스 직접 접근 시도
   - 검증: 외부에서 내부 서비스 접근 불가 확인 (방어 동작)
"""
import json
import urllib.request
import urllib.error
import time
import sys

TARGET = "http://webui.example-domain.com"

class ValidationResult:
    def __init__(self, finding_name, risk_type):
        self.finding_name = finding_name
        self.risk_type = risk_type
        self.steps = []
        self.vulnerable = False
        self.monitoring_detected = None

    def add_step(self, description, result, detail=""):
        self.steps.append({"desc": description, "result": result, "detail": detail})

    def summary(self):
        status = "취약" if self.vulnerable else "방어됨"
        mon = "감지됨" if self.monitoring_detected else ("미감지" if self.monitoring_detected is False else "미확인")
        return f"[{status}] {self.finding_name} | 모니터링: {mon}"


def validate_info_disclosure():
    """Finding: Server Error 500 - Stack Trace 노출"""
    r = ValidationResult("500 Error Info Disclosure", "INFORMATION_DISCLOSURE")

    # Step 1: GET /json — 재현
    try:
        req = urllib.request.Request(f"{TARGET}/json")
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode()
        r.add_step("GET /json", "200 OK", f"body: {body[:100]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        status = e.code

        # 검증 1: stack trace 노출 여부
        has_trace = "/usr/app/" in body or "node_modules" in body or ".js:" in body
        r.add_step(
            f"GET /json → {status}",
            "취약" if has_trace else "안전",
            f"Stack trace {'노출됨' if has_trace else '미노출'}"
        )
        if has_trace:
            r.vulnerable = True

        # 검증 2: 내부 경로 정보
        paths = [p for p in ["/usr/app/", "node_modules/router"] if p in body]
        r.add_step(
            "내부 경로 노출 확인",
            f"{len(paths)}개 경로 노출" if paths else "안전",
            ", ".join(paths)
        )

    except Exception as e:
        r.add_step("GET /json", "실패", str(e))

    return r


def validate_xpoweredby():
    """Finding: X-Powered-By 헤더 노출"""
    r = ValidationResult("X-Powered-By Header Exposure", "INFORMATION_DISCLOSURE")

    endpoints = ["/", "/index.html", "/nonexistent-page-test"]
    exposed_count = 0

    for ep in endpoints:
        try:
            req = urllib.request.Request(f"{TARGET}{ep}", method="HEAD")
            resp = urllib.request.urlopen(req, timeout=10)
            headers = dict(resp.headers)
            xpb = headers.get("X-Powered-By", "")
        except urllib.error.HTTPError as e:
            headers = dict(e.headers)
            xpb = headers.get("X-Powered-By", "")
        except Exception as e:
            r.add_step(f"HEAD {ep}", "실패", str(e))
            continue

        if xpb:
            exposed_count += 1
            r.add_step(f"HEAD {ep}", "취약", f"X-Powered-By: {xpb}")
        else:
            r.add_step(f"HEAD {ep}", "안전", "헤더 없음")

    r.vulnerable = exposed_count > 0
    return r


def validate_internal_isolation():
    """Finding: DoS /crash — 내부 서비스 격리 검증"""
    r = ValidationResult("Internal Service Isolation (/crash, /oom)", "DENIAL_OF_SERVICE")

    # 외부에서 내부 서비스 직접 접근 시도
    tests = [
        (f"{TARGET}/crash", "webui /crash"),
        (f"{TARGET}/oom", "webui /oom"),
        (f"{TARGET}/hasher/crash", "hasher via webui"),
    ]

    blocked_count = 0
    for url, desc in tests:
        try:
            req = urllib.request.Request(url, timeout=5)
            resp = urllib.request.urlopen(req)
            body = resp.read().decode()
            r.add_step(f"GET {desc}", "경고 - 접근 가능", body[:100])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                blocked_count += 1
                r.add_step(f"GET {desc}", "방어됨 (404)", "경로 미존재")
            elif e.code == 403:
                blocked_count += 1
                r.add_step(f"GET {desc}", "방어됨 (403)", "접근 거부")
            else:
                r.add_step(f"GET {desc}", f"HTTP {e.code}", "")
        except Exception as e:
            blocked_count += 1
            r.add_step(f"GET {desc}", "방어됨 (연결 불가)", str(e)[:80])

    r.vulnerable = blocked_count < len(tests)
    return r


def check_cloudwatch_alarm(finding_type):
    """CloudWatch에서 관련 알람 확인"""
    try:
        sys.path.insert(0, '.')
        from app_config import _boto_session
        session = _boto_session()
        cw = session.client("cloudwatch", region_name="us-east-1")

        # 최근 5분 내 5xx 알람 확인
        resp = cw.describe_alarms(StateValue="ALARM", MaxRecords=50)
        alarms = resp.get("MetricAlarms", [])

        related = []
        for a in alarms:
            name = a.get("AlarmName", "").lower()
            if "5xx" in name or "error" in name or "500" in name:
                related.append(a["AlarmName"])

        return related if related else None
    except Exception as e:
        return None


def run_all():
    print("=" * 60)
    print("Security Finding → 방어 검증 PoC")
    print(f"Target: {TARGET}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = [
        validate_info_disclosure(),
        validate_xpoweredby(),
        validate_internal_isolation(),
    ]

    # CloudWatch 알람 확인
    alarms = check_cloudwatch_alarm("5xx")

    print("\n--- 검증 결과 ---\n")
    for r in results:
        if alarms and "500" in r.finding_name:
            r.monitoring_detected = True
        print(r.summary())
        for s in r.steps:
            print(f"  {s['desc']:30} → {s['result']:20} {s['detail']}")
        print()

    # 종합 분석
    print("--- 종합 ---\n")
    vuln_count = sum(1 for r in results if r.vulnerable)
    safe_count = sum(1 for r in results if not r.vulnerable)
    print(f"취약: {vuln_count}건 / 방어됨: {safe_count}건")

    if alarms:
        print(f"CloudWatch 알람 (ALARM 상태): {alarms}")
    else:
        print("CloudWatch 알람: 활성 알람 없음")

    print("\n--- 인사이트 ---\n")
    for r in results:
        if r.vulnerable:
            print(f"⚠ [{r.finding_name}]")
            print(f"  → 취약점 현재 노출 중. PR 수정 필요.")
            if r.monitoring_detected is False:
                print(f"  → 모니터링 미감지: 알람 규칙 추가 필요")
        else:
            print(f"✓ [{r.finding_name}]")
            print(f"  → 방어 정상 동작 확인됨.")

    return results


if __name__ == "__main__":
    run_all()
