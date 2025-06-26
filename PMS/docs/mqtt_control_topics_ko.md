# PMS MQTT 토픽 및 제어 방법 가이드

본 문서는 PMS(Power Management System)에서 사용하는 MQTT 토픽 구조와 각 장비별 제어 방법을 정리한 가이드입니다.

---

## 1. MQTT 토픽 구조

```
pms/{device_type}/{device_name}/{channel}
```

| 세그먼트 | 설명 | 예시 |
|----------|------|------|
| `device_type` | 장비 유형 (PCS, BMS, DCDC) | `BMS` |
| `device_name` | `config.yml` 에 정의된 장비 이름 | `Rack1_BMS` |
| `channel` | `data` 또는 `control` | `data`, `control` |

### 1.1 데이터 토픽
- **형식** : `pms/{device_type}/{device_name}/data`
- **발행 주체** : PMS 애플리케이션
- **내용** : 장비에서 폴링한 실시간 계측치(가공 포함)

### 1.2 제어 토픽
- **형식** : `pms/{device_type}/{device_name}/control`
- **발행 주체** : 외부 시스템(운영 UI, 자동화 스크립트 등)
- **내용** : JSON 형식의 제어 명령

---

## 2. 공통 메시지 포맷

제어 토픽으로 발행되는 모든 메시지는 **UTF-8 인코딩 JSON** 문자열이어야 합니다.

```json
{
  "command": "<명령 키워드>",
  // 명령별 파라미터 …
}
```

`command` 필드는 필수이며, 각 장비 핸들러(`handle_control_message`)에서 이를 기준으로 실제 제어 로직을 실행합니다.

---

## 3. 장비별 지원 명령

### 3.1 BMS
| command | 설명 | 추가 파라미터 | 예시 |
|---------|------|--------------|------|
| `dc_contactor` | DC 접촉기 ON/OFF | `enable` : `true`/`false` | `{ "command": "dc_contactor", "enable": true }` |
| `reset_errors` | 에러 리셋 | 없음 | `{ "command": "reset_errors" }` |
| `reset_system_lock` | 시스템 락 리셋 | 없음 | `{ "command": "reset_system_lock" }` |

> **예시 토픽** : `pms/BMS/Rack1_BMS/control`
>
> **ON 명령 예시**
> ```json
> { "command": "dc_contactor", "enable": true }
> ```

### 3.2 PCS
| command | 설명 | 파라미터 | 비고 |
|---------|------|----------|------|
| `operation_mode` | PCS 운전 모드 설정 | `mode`: `"stop"`, `"charge"`, `"discharge"`, `"standby"` | 구현됨 |
| `power_reference` | 출력 전력 설정점 설정 | `power_kw`: 설정할 전력값(kW) | 구현됨 |
| `reset_faults` | PCS 고장 리셋 | 없음 | 구현됨 |

> **예시 토픽** : `pms/PCS/Farm_PCS/control`
>
> **운전 모드 설정 예시**
> ```json
> { "command": "operation_mode", "mode": "charge" }
> ```
>
> **출력 전력 설정 예시**
> ```json
> { "command": "power_reference", "power_kw": 10.5 }
> ```

### 3.3 DCDC
| command | 설명 | 파라미터 | 비고 |
|---------|------|----------|------|
| `operation_mode` | DCDC 운전 모드 설정 | `mode`: `"stop"`, `"standby"`, `"charge"`, `"discharge"`, `"independent"` | 구현됨 |
| `current_reference` | 출력 전류 설정점 설정 | `current_a`: 설정할 전류값(A) | 구현됨 |
| `voltage_reference` | 출력 전압 설정점 설정 | `voltage_v`: 설정할 전압값(V) | 구현됨 |
| `reset_faults` | DCDC 고장 리셋 | 없음 | 구현됨 |

> **예시 토픽** : `pms/DCDC/Farm_DCDC/control`
>
> **운전 모드 설정 예시**
> ```json
> { "command": "operation_mode", "mode": "charge" }
> ```
>
> **출력 전류 설정 예시**
> ```json
> { "command": "current_reference", "current_a": 15.2 }
> ```
>
> **출력 전압 설정 예시**
> ```json
> { "command": "voltage_reference", "voltage_v": 380.0 }
> ```

---

## 4. 구독/발행 예시

### 4.1 와일드카드 구독
- 사이트 전체 데이터 :  `pms/+/+/data`
- 특정 타입 모든 장비 제어 : `pms/PCS/+/control`

### 4.2 Mosquitto_pub 활용 예시
```bash
# BMS DC Contactor OFF
mosquitto_pub -h <broker-ip> -p 1883 -t "pms/BMS/Rack1_BMS/control" \
  -m '{"command":"dc_contactor","enable":false}'

# PCS 충전 모드 설정
mosquitto_pub -h <broker-ip> -p 1883 -t "pms/PCS/Farm_PCS/control" \
  -m '{"command":"operation_mode","mode":"charge"}'

# DCDC 출력 전압 설정
mosquitto_pub -h <broker-ip> -p 1883 -t "pms/DCDC/Farm_DCDC/control" \
  -m '{"command":"voltage_reference","voltage_v":380.0}'
```

---

## 5. 내부 동작 흐름
1. 외부 시스템이 **제어 토픽**으로 JSON 메시지 발행
2. `MQTTClient` 의 `on_message` → `message_callback` 콜백 호출
3. `main.py` 의 디스패처가 토픽과 일치하는 핸들러 검색
4. 해당 핸들러의 `handle_control_message(payload)` 비동기 실행
5. 핸들러가 Modbus `write_register` 등으로 실제 장치에 명령 수행

---

## 6. 확장 방법
- 새로운 명령을 추가하려면
  1. 해당 핸들러 클래스에 새로운 **async 메서드**(예: `async def set_mode(...)`) 구현
  2. `handle_control_message()` 에서 `command` 분기 추가
  3. 문서 표에 새로운 명령 설명 추가

- 새 장비 타입을 추가하려면
  1. `devices/새장비_handler.py` 구현( `DeviceInterface` 상속 )
  2. `DeviceFactory` 에 매핑 추가
  3. 위 규칙에 따라 데이터/제어 토픽 자동 생성

---

문의 사항은 개발팀에 알려주세요. 