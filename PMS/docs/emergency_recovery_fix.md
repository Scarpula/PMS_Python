# 긴급 복구 시 폴링 중단 문제 해결 가이드

## 문제 상황

### 증상
- 새벽 내내 스레드 수 초과 경고 발생 (73 > 50)
- CPU 사용률 88% 초과로 긴급 복구 실행
- `asyncio.CancelledError` 발생 후 폴링 루프 완전 중단
- Ctrl+C 수동 개입 없이는 재시작 불가

### 근본 원인
1. **과도한 임계값**: 스레드 수 50개는 너무 낮은 설정
2. **태스크 강제 종료**: 긴급 복구 시 모든 asyncio 태스크를 무조건 취소
3. **복구 불가능**: 취소된 태스크는 재시작되지 않음
4. **CancelledError 미처리**: `monitor_server`의 `asyncio.sleep()`이 취소되어 루프 중단

## 적용된 해결책

### 1. 모니터링 임계값 상향 조정 ✅

**변경 전:**
```yaml
monitoring:
  memory_threshold_mb: 500
  cpu_threshold_percent: 80
  thread_threshold: 50
```

**변경 후:**
```yaml
monitoring:
  memory_threshold_mb: 800        # +60% 증가
  cpu_threshold_percent: 90       # +12.5% 증가
  thread_threshold: 100           # +100% 증가
```

**효과**: 정상 운영 중 불필요한 복구 트리거 방지

---

### 2. 복구 시도 정책 완화 ✅

**변경 전:**
```yaml
recovery:
  max_attempts: 3
  cooldown_seconds: 60
```

**변경 후:**
```yaml
recovery:
  max_attempts: 5                 # 3 → 5회
  cooldown_seconds: 120           # 60 → 120초
```

**효과**: 일시적 문제에 대한 재시도 기회 증가

---

### 3. CancelledError 처리 개선 ✅

**위치**: `main_gui_integrated.py:monitor_server()`

**변경 내용:**
```python
while self.server_running:
    try:
        await asyncio.sleep(30)
        # ... 상태 모니터링 로직 ...

    except asyncio.CancelledError:
        print("🩺 건강성 체크: 연결 끊어짐 감지")
        # CancelledError는 정상적인 종료 시그널
        raise  # 루프 종료

    except Exception as e:
        # 기타 오류는 계속 진행
        print(f"⚠️ 서버 모니터링 오류: {e}")
        await asyncio.sleep(5)
```

**효과**:
- CancelledError를 명시적으로 처리
- 정상 종료 vs 오류를 구분
- 불필요한 재시도 방지

---

### 4. 긴급 복구 로직 개선 ✅

**위치**: `system_monitor.py:_emergency_recovery()`

**변경 전:**
```python
async def _emergency_recovery(self):
    # 긴급 복구 핸들러 실행
    if self.emergency_handler:
        await self.emergency_handler()

    # 모든 태스크 강제 종료 ❌
    tasks = [t for t in asyncio.all_tasks() ...]
    for task in tasks:
        task.cancel()
```

**변경 후:**
```python
async def _emergency_recovery(self):
    # 긴급 복구 핸들러 실행
    if self.emergency_handler:
        await self.emergency_handler()

    # restart_on_emergency=false인 경우 태스크 유지 ✅
    if self.config.get('recovery', {}).get('restart_on_emergency', False):
        # 재시작이 필요한 경우에만 태스크 종료
        tasks = [t for t in asyncio.all_tasks() ...]
        for task in tasks:
            task.cancel()
        sys.exit(1)
    else:
        # 태스크는 계속 실행
        self.logger.warning("태스크는 계속 실행됩니다")
```

**효과**: 태스크 종료 없이 복구만 수행

---

### 5. 자동 복구 카운터 리셋 ✅

**위치**: `system_monitor.py:_auto_recovery()`

**추가 로직:**
```python
# 복구 성공 시 일정 시간 후 복구 시도 횟수 리셋
await asyncio.sleep(self.recovery_cooldown)
if self.recovery_attempts > 0:
    self.logger.info(f"복구 시도 횟수 리셋 (현재: {self.recovery_attempts} → 0)")
    self.recovery_attempts = 0
```

**효과**: 복구 성공 후 카운터 초기화로 무한 복구 시도 방지

---

### 6. 스케줄러 건강성 체크 강화 ✅

**위치**: `scheduler.py:_health_check_loop()`

**추가 기능:**
```python
consecutive_healthy_checks = 0
reset_threshold = 6  # 6회 연속 정상 (3분)

while self.running:
    # ... 건강성 체크 ...

    if unhealthy_devices:
        consecutive_healthy_checks = 0
    else:
        consecutive_healthy_checks += 1

    # 연속 정상 시 장비별 오류 카운터 리셋
    if consecutive_healthy_checks >= reset_threshold:
        for device_task in self.device_tasks.values():
            if device_task.consecutive_errors > 0:
                device_task.consecutive_errors = 0
                device_task.is_healthy = True
        consecutive_healthy_checks = 0
```

**효과**:
- 일시적 오류 후 자동 복구
- 장비별 오류 카운터 자동 리셋
- 불필요한 비정상 상태 방지

---

## 설정 파일 최종 권장값

### config.yml
```yaml
# 시스템 모니터링 설정
monitoring:
  enabled: true
  check_interval: 10
  memory_threshold_mb: 800        # 충분한 여유
  cpu_threshold_percent: 90       # 고부하 허용
  thread_threshold: 100           # Windows 환경 고려

# 자동 복구 설정
recovery:
  enabled: true
  max_attempts: 5                 # 재시도 여유
  cooldown_seconds: 120           # 충분한 대기
  restart_on_emergency: false     # 태스크 유지
```

---

## 검증 방법

### 1. 정상 동작 확인
```
🟢 PMS 서버 정상 동작 중 (장비: 3개)
✅ 모든 장비 정상 상태 (연속 6회)
🔄 복구 시도 횟수 리셋 (현재: 2 → 0)
```

### 2. 경고 발생 시
```
⚠️ 시스템 건강 상태: warning
   - 스레드 수 초과: 73 > 100  # 임계값 상향
🔧 자동 복구 시작 (시도 1/5)
✅ 자동 복구 완료
```

### 3. 긴급 상황 시
```
🚨 최대 복구 시도 횟수 초과
🚨 긴급 복구 시스템 활성화
⚠️ 태스크는 계속 실행됩니다  # 폴링 유지
```

---

## 추가 권장사항

### 1. 로그 모니터링
- 스레드 수 추이 관찰
- CPU/메모리 사용 패턴 분석
- 복구 시도 빈도 확인

### 2. 환경별 튜닝
- **개발 환경**: 낮은 임계값으로 테스트
- **운영 환경**: 높은 임계값으로 안정성 우선
- **저사양 시스템**: thread_threshold를 더 낮게 조정

### 3. 정기 재시작
- 24시간마다 자동 재시작 스케줄링 권장
- Windows 작업 스케줄러 활용

### 4. 모니터링 대시보드
- 시스템 메트릭 실시간 모니터링
- 복구 이벤트 알림 설정
- 장비별 건강성 추이 분석

---

## 문제 발생 시 대응

### 폴링이 여전히 중단되는 경우

1. **로그 확인**
```bash
# 긴급 복구 발생 여부
grep "긴급 복구" logs/pms.log

# CancelledError 발생 위치
grep "CancelledError" logs/pms.log
```

2. **임계값 재조정**
```yaml
# 더 높은 임계값으로 변경
thread_threshold: 150
cpu_threshold_percent: 95
```

3. **복구 비활성화 테스트**
```yaml
# 일시적으로 자동 복구 비활성화
recovery:
  enabled: false
```

4. **수동 재시작**
```bash
# Windows
taskkill /F /IM main_gui_integrated.exe
python main_gui_integrated.py

# 또는 프로그램 재시작
```

---

## 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|-----------|
| 2026-01-14 | 1.0 | 초기 수정: 임계값 상향, CancelledError 처리, 긴급 복구 개선 |

---

## 관련 파일

- `main_gui_integrated.py:202-250` - monitor_server 메서드
- `system_monitor.py:417-446` - _emergency_recovery 메서드
- `system_monitor.py:368-421` - _auto_recovery 메서드
- `scheduler.py:291-347` - _health_check_loop 메서드
- `config.yml:66-79` - 모니터링 및 복구 설정
