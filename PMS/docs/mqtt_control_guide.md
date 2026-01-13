# PMS MQTT 제어 가이드

## 개요
이 문서는 백엔드 시스템에서 PMS의 각 장비(BMS, DCDC, PCS)를 MQTT를 통해 제어하기 위한 완전한 가이드입니다.

## 1. 기본 토픽 구조

### 1.1 장비별 개별 제어 토픽
- **제어 요청**: `pms/control/{device_name}/command`
- **제어 응답**: `pms/control/{device_name}/response`

### 1.2 시스템 운전 모드 제어 토픽 (Location 기반)
- **운전 모드 전환**: `pms/control/{location}/operation_mode`
- **자동 모드 시작**: `pms/control/{location}/auto_mode/start`
- **자동 모드 정지**: `pms/control/{location}/auto_mode/stop`
- **자동 모드 상태**: `pms/control/{location}/auto_mode/status`
- **수동 모드 제어**: `pms/control/{location}/basic_mode`
- **임계값 설정**: `pms/control/{location}/threshold_config`

**Location 값:**
- `대실마을`: 대실마을 PMS 시스템
- `한자연`: 한자연 PMS 시스템

## 2. 장비별 제어 메시지 양식

### 2.1 제어 요청 메시지 (Command) 형식
```json
{
    "action": "write_register",
    "address": 200,
    "value": 1,
    "description": "DC 접촉기 ON",
    "timestamp": "2025-07-16T10:44:25.665337",
    "gui_request_id": "대실마을_BMS_200_1752630265665387"
}
```

**필수 필드:**
- `action`: 항상 "write_register"
- `address`: 제어할 레지스터 주소 (정수)
- `value`: 쓸 값 (정수)
- `description`: 제어 동작 설명 (문자열)
- `timestamp`: ISO 8601 형식의 타임스탬프
- `gui_request_id`: 고유 요청 ID (응답 매칭용)

### 2.2 제어 응답 메시지 (Response) 형식
```json
{
    "request_id": "대실마을_BMS_200_1752630265665387",
    "success": true,
    "message": "성공: DC 접촉기 ON",
    "timestamp": "2025-07-16T10:44:31.838002",
    "device_name": "대실마을_BMS"
}
```

**응답 필드:**
- `request_id`: 요청 메시지의 gui_request_id와 동일
- `success`: 제어 성공 여부 (boolean)
- `message`: 결과 메시지 (성공/실패 정보)
- `timestamp`: 응답 생성 시각
- `device_name`: 대상 장비 이름

## 3. 장비별 제어 레지스터 정보

### 3.1 BMS 제어 레지스터 (대실마을_BMS)

#### 토픽: `pms/control/대실마을_BMS/command`

| 레지스터 이름 | 주소 | 값 | 설명 |
|---|---|---|---|
| `dc_contactor_control` | 200 | 1 | DC 접촉기 ON |
| `dc_contactor_control` | 200 | 0 | DC 접촉기 OFF |
| `error_reset` | 201 | 128 (0x80) | 에러 리셋 |
| `system_lock_reset` | 202 | 128 (0x80) | 시스템 락 리셋 |
| `ip_address_ab` | 203 | 값 | IP 주소 [A.B] 설정 |
| `ip_address_cd` | 204 | 값 | IP 주소 [C.D] 설정 |
| `rbms_reset` | 205 | 43605 (0xAA55) | RBMS 리셋 |

#### 예시 메시지:
```json
{
    "action": "write_register",
    "address": 200,
    "value": 1,
    "description": "DC 접촉기 ON",
    "timestamp": "2025-07-16T10:44:25.665337",
    "gui_request_id": "backend_bms_200_1752630265665387"
}
```

### 3.2 DCDC 제어 레지스터 (대실마을_DCDC)

#### 토픽: `pms/control/대실마을_DCDC/command`

| 레지스터 이름 | 주소 | 값 | 설명 |
|---|---|---|---|
| `reset_command` | 100 | 85 (0x55) | 리셋 명령 |
| `stop_command` | 101 | 85 (0x55) | 정지 명령 |
| `ready_command` | 102 | 85 (0x55) | 준비 명령 |
| `charge_command` | 103 | 85 (0x55) | 충전 명령 |
| `regen_command` | 104 | 85 (0x55) | 회생 명령 |
| `start_command` | 105 | 85 (0x55) | 시작 명령 (독립 운전) |
| `ready_standby_command` | 106 | 값 | 대기 모드 준비 |
| `solar_command` | 107 | 값 | 태양광 충전 모드 |

#### 예시 메시지:
```json
{
    "action": "write_register",
    "address": 103,
    "value": 85,
    "description": "DCDC 충전 시작",
    "timestamp": "2025-07-16T10:44:25.665337",
    "gui_request_id": "backend_dcdc_103_1752630265665387"
}
```

### 3.3 PCS 제어 레지스터 (대실마을_PCS)

#### 토픽: `pms/control/대실마을_PCS/command`

| 레지스터 이름 | 주소 | 값 | 설명 |
|---|---|---|---|
| `pcs_reset` | 19 | 85 (0x55) | PCS 리셋 |
| `pcs_stop` | 20 | 85 (0x55) | PCS 정지 |
| `pcs_standby_start` | 21 | 85 (0x55) | PCS 대기 시작 |
| `pcs_charge_start` | 22 | 85 (0x55) | PCS 충전 시작 |
| `pcs_regen_start` | 23 | 85 (0x55) | PCS 회생 시작 |
| `inv_start_mode` | 24 | 85 (0x55) | 독립 운전 모드 |
| `bms_contactor` | 26 | 85 (0x55) | BMS 접촉기 모두 ON |
| `bms_contactor` | 26 | 170 (0xAA) | BMS 접촉기 모두 OFF |

#### 예시 메시지:
```json
{
    "action": "write_register",
    "address": 22,
    "value": 85,
    "description": "PCS 충전 시작",
    "timestamp": "2025-07-16T10:44:25.665337",
    "gui_request_id": "backend_pcs_22_1752630265665387"
}
```

## 4. 시스템 운전 모드 제어

### 4.0 Location 기반 제어 개요
PMS 시스템은 여러 위치에 설치될 수 있으며, 각 위치별로 독립적인 운전 모드 제어가 필요합니다.

**현재 지원되는 위치:**
- `대실마을`: 대실마을 PMS 시스템
- `한자연`: 한자연 PMS 시스템

**토픽 구조:**
- 각 PMS 시스템은 자신의 location이 포함된 토픽만 구독합니다.
- 예: 대실마을 PMS는 `pms/control/대실마을/*` 토픽만 구독
- 예: 한자연 PMS는 `pms/control/한자연/*` 토픽만 구독

**토픽 자동 설정:**
각 PMS 시스템의 `config.yml` 파일에서 `database.device_location` 값을 기반으로 토픽이 자동 설정됩니다:
```yaml
database:
  device_location: "대실마을"  # 또는 "한자연"
```

**장점:**
- 특정 location만 제어 가능 (메시지 충돌 방지)
- 각 PMS 시스템의 독립성 보장
- 토픽 구조로 명확한 대상 식별

### 4.1 운전 모드 전환

**대실마을 PMS 제어 토픽:** `pms/control/대실마을/operation_mode`
**한자연 PMS 제어 토픽:** `pms/control/한자연/operation_mode`

```json
{
    "mode": "auto",
    "timestamp": "2025-07-16T10:44:25.665337"
}
```

**모드 옵션:**
- `"auto"`: 자동 운전 모드
- `"manual"`: 수동 운전 모드

**사용 예시:**
```python
# 대실마을 PMS 자동 모드로 전환
client.publish("pms/control/대실마을/operation_mode", json.dumps({
    "mode": "auto",
    "timestamp": datetime.now().isoformat()
}))

# 한자연 PMS 수동 모드로 전환
client.publish("pms/control/한자연/operation_mode", json.dumps({
    "mode": "manual",
    "timestamp": datetime.now().isoformat()
}))
```

### 4.2 자동 모드 제어

**대실마을 PMS 시작 토픽:** `pms/control/대실마을/auto_mode/start`
**한자연 PMS 시작 토픽:** `pms/control/한자연/auto_mode/start`

```json
{
    "command": "start",
    "timestamp": "2025-07-16T10:44:25.665337"
}
```

**대실마을 PMS 정지 토픽:** `pms/control/대실마을/auto_mode/stop`
**한자연 PMS 정지 토픽:** `pms/control/한자연/auto_mode/stop`

```json
{
    "command": "stop",
    "timestamp": "2025-07-16T10:44:25.665337"
}
```

**사용 예시:**
```python
# 대실마을 PMS 자동 모드 시작
client.publish("pms/control/대실마을/auto_mode/start", json.dumps({
    "command": "start",
    "timestamp": datetime.now().isoformat()
}))

# 한자연 PMS 자동 모드 정지
client.publish("pms/control/한자연/auto_mode/stop", json.dumps({
    "command": "stop",
    "timestamp": datetime.now().isoformat()
}))
```

### 4.3 임계값 설정

**대실마을 PMS 임계값 토픽:** `pms/control/대실마을/threshold_config`
**한자연 PMS 임계값 토픽:** `pms/control/한자연/threshold_config`

```json
{
    "soc_upper_limit": 90,
    "soc_lower_limit": 10,
    "timestamp": "2025-07-16T10:44:25.665337"
}
```

**사용 예시:**
```python
# 대실마을 PMS 임계값 설정
client.publish("pms/control/대실마을/threshold_config", json.dumps({
    "soc_upper_limit": 90,
    "soc_lower_limit": 10,
    "timestamp": datetime.now().isoformat()
}))

# 한자연 PMS 임계값 설정
client.publish("pms/control/한자연/threshold_config", json.dumps({
    "soc_upper_limit": 85,
    "soc_lower_limit": 15,
    "timestamp": datetime.now().isoformat()
}))
```

## 5. 오류 처리 및 타임아웃

### 5.1 제어 타임아웃
- 모든 제어 명령은 **10초 타임아웃**이 적용됩니다.
- 타임아웃 발생 시 응답 메시지:
```json
{
    "request_id": "backend_bms_200_1752630265665387",
    "success": false,
    "message": "제어 명령 타임아웃: 10초",
    "timestamp": "2025-07-16T10:44:35.665337",
    "device_name": "대실마을_BMS"
}
```

### 5.2 잘못된 주소 또는 값
```json
{
    "request_id": "backend_bms_999_1752630265665387",
    "success": false,
    "message": "주소 999에 해당하는 레지스터를 찾을 수 없음",
    "timestamp": "2025-07-16T10:44:31.838002",
    "device_name": "대실마을_BMS"
}
```

## 6. 실제 사용 예제

### 6.1 BMS DC 접촉기 제어
```python
import json
import paho.mqtt.client as mqtt
from datetime import datetime

# MQTT 클라이언트 설정
client = mqtt.Client()
client.connect("mqtt_broker_ip", 1883, 60)

# 제어 명령 전송
command = {
    "action": "write_register",
    "address": 200,
    "value": 1,
    "description": "DC 접촉기 ON",
    "timestamp": datetime.now().isoformat(),
    "gui_request_id": f"backend_bms_200_{int(datetime.now().timestamp() * 1000)}"
}

client.publish("pms/control/대실마을_BMS/command", json.dumps(command))

# 응답 구독
def on_message(client, userdata, message):
    response = json.loads(message.payload.decode())
    print(f"제어 결과: {response}")

client.subscribe("pms/control/대실마을_BMS/response")
client.on_message = on_message
client.loop_start()
```

### 6.2 여러 장비 동시 제어
```python
devices = [
    {
        "name": "대실마을_BMS",
        "address": 200,
        "value": 1,
        "description": "DC 접촉기 ON"
    },
    {
        "name": "대실마을_DCDC", 
        "address": 103,
        "value": 85,
        "description": "충전 시작"
    },
    {
        "name": "대실마을_PCS",
        "address": 22,
        "value": 85,
        "description": "충전 시작"
    }
]

for device in devices:
    command = {
        "action": "write_register",
        "address": device["address"],
        "value": device["value"],
        "description": device["description"],
        "timestamp": datetime.now().isoformat(),
        "gui_request_id": f"backend_{device['name']}_{device['address']}_{int(datetime.now().timestamp() * 1000)}"
    }
    
    topic = f"pms/control/{device['name']}/command"
    client.publish(topic, json.dumps(command))
```

### 6.3 Location 기반 자동 모드 제어
```python
# 대실마을 PMS 자동 모드 시작
daesil_auto_start = {
    "command": "start",
    "timestamp": datetime.now().isoformat()
}
client.publish("pms/control/대실마을/auto_mode/start", json.dumps(daesil_auto_start))

# 한자연 PMS 자동 모드 시작
hanja_auto_start = {
    "command": "start",
    "timestamp": datetime.now().isoformat()
}
client.publish("pms/control/한자연/auto_mode/start", json.dumps(hanja_auto_start))

# 특정 location의 운전 모드 변경
operation_mode_change = {
    "mode": "auto",
    "timestamp": datetime.now().isoformat()
}
client.publish("pms/control/대실마을/operation_mode", json.dumps(operation_mode_change))

# 여러 location 동시 제어
locations = ["대실마을", "한자연"]
for location in locations:
    # 각 location별 자동 모드 시작
    auto_start_msg = {
        "command": "start",
        "timestamp": datetime.now().isoformat()
    }
    client.publish(f"pms/control/{location}/auto_mode/start", json.dumps(auto_start_msg))
```

## 7. 주의사항

1. **요청 ID 고유성**: `gui_request_id`는 반드시 고유해야 합니다.
2. **타임스탬프 형식**: ISO 8601 형식 사용 필수
3. **값 범위**: 각 레지스터의 허용 값 범위를 확인하세요.
4. **네트워크 상태**: 장비가 오프라인인 경우 연결 실패 응답이 반환됩니다.
5. **동시 제어**: 같은 장비에 대한 동시 제어는 순차적으로 처리됩니다.
6. **Location 기반 토픽**: 
   - 시스템 운전 모드 제어시 반드시 정확한 location을 토픽에 포함하세요.
   - 잘못된 location 토픽은 해당 PMS에서 구독하지 않습니다.
   - 각 PMS는 자신의 location 토픽만 구독하므로 정확한 토픽 사용이 중요합니다.

## 8. 테스트 도구

### 8.1 연결 테스트
**토픽:** `pms/test/connection`
```json
{
    "test": "connection",
    "timestamp": "2025-07-16T10:44:25.665337"
}
```

이 메시지를 발행하면 시스템이 정상적으로 MQTT 메시지를 수신하고 있는지 확인할 수 있습니다.

---

**문서 버전**: 1.0  
**최종 수정**: 2025-07-16  
**작성자**: PMS 시스템 개발팀 