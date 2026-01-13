# 자동 복구 시스템 가이드

## 개요

PMS가 오랫동안 꺼졌다가 켜질 경우 BMS에서 Communication Error가 발생할 수 있습니다.
이 자동 복구 시스템은 해당 에러를 자동으로 감지하고 복구 시퀀스를 실행합니다.

## Communication Error 감지

### BMS Error Code 2 분석

- **레지스터**: Error Code 2 (Address: 0x0056)
- **감지 비트**: Bit 3 (b3)
- **에러명**: MODBUS Communication Error [Single Rack : RBMS-PMS]
- **Decimal 값**: 8 (0b0000_0000_0000_1000)

### 에러 발생 조건

1. PMS가 장시간 꺼져 있다가 재시작된 경우
2. BMS와 PMS 간 통신이 일시적으로 끊어졌던 경우
3. 네트워크 연결이 불안정한 상태에서 시스템이 시작된 경우

## 자동 복구 시퀀스

### 복구 순서

시스템은 다음 순서로 자동 복구를 수행합니다:

```
1. BMS 에러 리셋
   ↓ (2초 대기)
2. BMS DC 컨택터 ON
   ↓ (3초 대기)
3. PCS 리셋
   ↓ (2초 대기)
4. PCS 독립운전 모드 실행
```

### 각 단계 상세 설명

#### 1단계: BMS 에러 리셋
- **명령**: `bms_handler.reset_errors()`
- **목적**: BMS에 축적된 Communication Error 클리어
- **대기 시간**: 2초 (BMS 내부 처리 시간)

#### 2단계: BMS DC 컨택터 ON
- **명령**: `bms_handler.control_dc_contactor(True)`
- **목적**: DC 전원 경로 활성화
- **대기 시간**: 3초 (컨택터 물리적 동작 시간)

#### 3단계: PCS 리셋
- **명령**: `pcs_handler.reset_faults()`
- **목적**: PCS 고장 상태 리셋
- **대기 시간**: 2초 (PCS 내부 처리 시간)

#### 4단계: PCS 독립운전 모드 실행
- **명령**: `pcs_handler.set_operation_mode('independent')`
- **목적**: PCS를 독립운전 모드로 전환
- **대기 시간**: 없음 (마지막 단계)

## 시스템 동작

### 자동 감시 주기

- **체크 간격**: 30초마다
- **초기 대기**: 시스템 시작 후 10초 (안정화 시간)
- **복구 후 대기**: 60초 (시스템 안정화)

### 동작 조건

자동 복구는 다음 조건이 모두 만족될 때만 동작합니다:

1. ✅ BMS 핸들러가 존재
2. ✅ PCS 핸들러가 존재
3. ✅ BMS가 연결된 상태 (`bms_handler.connected == True`)
4. ✅ BMS 데이터 읽기 성공
5. ✅ Error Code 2의 Bit 3이 1인 상태
6. ✅ 현재 복구 작업이 진행 중이지 않음

### 로그 예시

#### 정상 복구 시

```
⚠️ BMS Communication Error 감지: Error Code 2 = 8 (0x0008, Binary: 0000000000001000), Bit 3 = 1
🔄 BMS Communication Error 감지 - 자동 복구 시퀀스 시작
============================================================
🔧 자동 복구 시퀀스 시작
============================================================
1️⃣ BMS 에러 리셋 실행
✅ BMS 에러 리셋 성공
2️⃣ BMS DC 컨택터 ON 실행
✅ BMS DC 컨택터 ON 성공
3️⃣ PCS 리셋 실행
✅ PCS 리셋 성공
4️⃣ PCS 독립운전 모드 실행
✅ PCS 독립운전 모드 실행 성공
============================================================
✅ 자동 복구 시퀀스 모든 단계 완료
============================================================
✅ 자동 복구 시퀀스 완료 (총 1회)
⏳ 복구 후 시스템 안정화 대기 (60초)
```

#### 복구 실패 시

```
⚠️ BMS Communication Error 감지: Error Code 2 = 8 (0x0008, Binary: 0000000000001000), Bit 3 = 1
🔄 BMS Communication Error 감지 - 자동 복구 시퀀스 시작
============================================================
🔧 자동 복구 시퀀스 시작
============================================================
1️⃣ BMS 에러 리셋 실행
✅ BMS 에러 리셋 성공
2️⃣ BMS DC 컨택터 ON 실행
❌ BMS DC 컨택터 ON 실패
❌ 자동 복구 시퀀스 실패
```

## 아키텍처

### 클래스 구조

```
OperationManager
├── AutoRecoveryManager (자동 복구 관리자)
│   ├── check_and_recover() - 에러 확인 및 복구 실행
│   └── _execute_recovery_sequence() - 복구 시퀀스 실행
└── _auto_recovery_monitor() - 주기적 감시 태스크
```

### 파일 구조

```
PMS/pms_app/automation/
├── auto_recovery.py          # 자동 복구 로직
├── operation_manager.py      # 운전 모드 관리자 (감시 태스크 포함)
└── __init__.py              # 모듈 초기화
```

## 설정

### 활성화 조건

자동 복구 시스템은 다음 조건이 만족되면 자동으로 활성화됩니다:

1. `device_handlers`에 'BMS' 핸들러 존재
2. `device_handlers`에 'PCS' 핸들러 존재

### 비활성화

BMS 또는 PCS 핸들러가 없으면 자동으로 비활성화되며, 다음 경고 로그가 출력됩니다:

```
⚠️ BMS 또는 PCS 핸들러가 없어 자동 복구 비활성화
```

## 상태 확인

### 자동 복구 상태 조회

```python
status = operation_manager.auto_recovery.get_status()
```

**반환 값**:
```python
{
    'recovery_in_progress': False,      # 현재 복구 진행 중 여부
    'total_recovery_count': 3,          # 총 복구 실행 횟수
    'last_recovery_attempt': '2025-11-19T10:30:00'  # 마지막 복구 시도 시간
}
```

## 문제 해결

### 자동 복구가 동작하지 않을 때

#### 1. BMS/PCS 핸들러 확인
```
로그에서 다음 메시지 확인:
🔧 자동 복구 관리자 활성화
```

만약 다음 메시지가 보이면 핸들러가 없는 것입니다:
```
⚠️ BMS 또는 PCS 핸들러가 없어 자동 복구 비활성화
```

#### 2. BMS 연결 상태 확인
```
로그에서 다음 메시지가 반복되면 BMS가 연결되지 않은 것입니다:
BMS가 연결되지 않아 자동 복구 감시 스킵
```

**해결 방법**:
- BMS 네트워크 연결 확인
- BMS IP 주소 설정 확인
- BMS 전원 상태 확인

#### 3. Communication Error가 감지되지 않을 때

**Error Code 2 값 확인**:
```python
# BMS 데이터에서 error_code_2 확인
bms_data = await bms_handler.read_data()
error_code_2 = bms_data.get('error_code_2')
print(f"Error Code 2: {error_code_2} (Binary: {bin(error_code_2)[2:].zfill(16)})")
```

Bit 3이 1이어야 Communication Error로 인식됩니다.

### 복구가 반복 실패할 때

#### 1. 각 단계별 확인

**BMS 에러 리셋 실패**:
- BMS Modbus 연결 상태 확인
- BMS 에러 리셋 레지스터 주소 확인

**DC 컨택터 ON 실패**:
- 컨택터 제어 레지스터 주소 확인
- 하드웨어 컨택터 상태 확인

**PCS 리셋 실패**:
- PCS Modbus 연결 상태 확인
- PCS 리셋 레지스터 주소 확인

**독립운전 모드 실행 실패**:
- PCS 독립운전 모드 지원 여부 확인
- PCS 상태 확인 (다른 모드에서 전환 가능 상태인지)

#### 2. 수동 복구 시도

자동 복구가 실패하는 경우, 수동으로 단계별 실행:

```python
# 1. BMS 에러 리셋
await bms_handler.reset_errors()

# 2. BMS DC 컨택터 ON
await bms_handler.control_dc_contactor(True)

# 3. PCS 리셋
await pcs_handler.reset_faults()

# 4. PCS 독립운전 모드
await pcs_handler.set_operation_mode('independent')
```

## 테스트

### 시뮬레이션 테스트

Communication Error 상태를 시뮬레이션하여 자동 복구 테스트:

```python
# 테스트용 BMS 데이터 생성 (Communication Error 포함)
test_bms_data = {
    'error_code_2': 8  # Bit 3 = 1 (Communication Error)
}

# 자동 복구 테스트
recovery_attempted = await auto_recovery.check_and_recover(test_bms_data)
print(f"복구 시도 여부: {recovery_attempted}")
```

### 통합 테스트

실제 시스템에서 테스트:

1. PMS 시스템 완전 종료
2. 1분 이상 대기
3. PMS 시스템 재시작
4. 로그에서 자동 복구 시퀀스 실행 확인

## 보안 및 안전

### 중복 실행 방지

- `recovery_in_progress` 플래그로 중복 실행 방지
- 하나의 복구 시퀀스만 동시 실행 가능

### 타임아웃

각 단계마다 적절한 대기 시간 설정:
- BMS 에러 리셋 후: 2초
- DC 컨택터 ON 후: 3초
- PCS 리셋 후: 2초
- 전체 복구 후: 60초

### 실패 시 대응

- 각 단계 실패 시 즉시 복구 시퀀스 중단
- 실패 로그 기록
- 다음 주기(30초 후)에 재시도

## 향후 개선 사항

### 계획된 기능

1. **복구 재시도 제한**: 일정 횟수 이상 실패 시 자동 복구 중단
2. **MQTT 알림**: 복구 성공/실패 시 MQTT 메시지 발행
3. **통계 기록**: 복구 성공률, 평균 복구 시간 등 통계 수집
4. **설정 가능한 대기 시간**: 각 단계별 대기 시간을 config.yml에서 설정
5. **수동 복구 트리거**: MQTT를 통한 수동 복구 명령 지원

### 확장 가능성

- 다른 에러 코드에 대한 자동 복구 로직 추가
- 다중 에러 동시 발생 시 우선순위 기반 복구
- AI 기반 에러 패턴 분석 및 예측
