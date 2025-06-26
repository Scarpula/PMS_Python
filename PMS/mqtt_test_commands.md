# MQTT 테스트 명령어

이 파일은 PMS 시스템의 자동 운전 모드와 기본 운전 모드를 테스트하기 위한 MQTT 명령어 예시를 제공합니다.

## 운전 모드 제어

### 1. 기본 운전 모드로 전환
```bash
mosquitto_pub -h 139.150.70.42 -t "pms/control/operation_mode" -m '{"mode": "basic"}'
```

### 2. 자동 운전 모드로 전환
```bash
mosquitto_pub -h 139.150.70.42 -t "pms/control/operation_mode" -m '{"mode": "auto"}'
```

## 자동 운전 모드 제어

### 1. 자동 모드 시작
```bash
mosquitto_pub -h 139.150.70.42 -t "pms/control/auto_mode/start" -m '{}'
```

### 2. 자동 모드 정지
```bash
mosquitto_pub -h 139.150.70.42 -t "pms/control/auto_mode/stop" -m '{}'
```

### 3. 자동 모드 상태 조회
```bash
mosquitto_pub -h 139.150.70.42 -t "pms/control/auto_mode/status" -m '{}'
```

## 기본 모드 제어 (개별 장비)

### 1. PCS 제어
```bash
# PCS 정지
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_PCS", "command": "stop"}'

# PCS 대기 모드
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_PCS", "command": "standby"}'

# PCS 독립 운전 모드
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_PCS", "command": "inverter"}'

# PCS 충전 모드
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_PCS", "command": "charge"}'
```

### 2. DCDC 제어
```bash
# DCDC 리셋
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_DCDC", "command": "reset"}'

# DCDC 태양광 모드
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_DCDC", "command": "solar"}'

# DCDC 대기 모드
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_DCDC", "command": "standby"}'
```

### 3. BMS 제어
```bash
# BMS 컨택터 제어
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_BMS", "command": "contactor", "value": true}'

# BMS 리셋
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_BMS", "command": "reset"}'
```

## 상태 모니터링

### 1. 모든 토픽 구독
```bash
mosquitto_sub -h 139.150.70.42 -t "pms/#" -v
```

### 2. 데이터만 구독
```bash
mosquitto_sub -h 139.150.70.42 -t "pms/+/+/data" -v
```

### 3. 제어 응답만 구독
```bash
mosquitto_sub -h 139.150.70.42 -t "pms/status/operation_mode/response" -v
```

### 4. 운전 모드 상태만 구독
```bash
mosquitto_sub -h 139.150.70.42 -t "pms/status/operation_mode" -v
```

## 테스트 시나리오

### 시나리오 1: 자동 운전 모드 전체 테스트
```bash
# 1. 자동 모드로 전환
mosquitto_pub -h 139.150.70.42 -t "pms/control/operation_mode" -m '{"mode": "auto"}'

# 2. 자동 모드 시작
mosquitto_pub -h 139.150.70.42 -t "pms/control/auto_mode/start" -m '{}'

# 3. 상태 확인 (약 30초 후)
mosquitto_pub -h 139.150.70.42 -t "pms/control/auto_mode/status" -m '{}'

# 4. 자동 모드 정지
mosquitto_pub -h 139.150.70.42 -t "pms/control/auto_mode/stop" -m '{}'
```

### 시나리오 2: 기본 운전 모드 테스트
```bash
# 1. 기본 모드로 전환
mosquitto_pub -h 139.150.70.42 -t "pms/control/operation_mode" -m '{"mode": "basic"}'

# 2. PCS 독립 운전 모드 설정
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_PCS", "command": "inverter"}'

# 3. DCDC 태양광 모드 설정
mosquitto_pub -h 139.150.70.42 -t "pms/control/basic_mode" -m '{"device": "한자연_DCDC", "command": "solar"}'
```

### 시나리오 3: SOC 기반 자동 제어 테스트
```bash
# 자동 모드 시작 후, BMS 데이터에서 SOC 값을 확인하여
# 88% 이상 또는 5% 이하일 때 자동 제어 동작을 관찰

# SOC 모니터링
mosquitto_sub -h 139.150.70.42 -t "pms/BMS/한자연_BMS/data" -v | grep -i soc
```

## 주의사항

1. **실제 장비 연결 시 주의**: 위 명령어들은 실제 PCS, DCDC, BMS 장비에 제어 신호를 보냅니다.
2. **안전 확인**: 장비 제어 전 현재 상태를 확인하고 안전한 상황에서만 실행하세요.
3. **네트워크 설정**: MQTT 브로커 주소와 장비 IP 주소가 올바른지 확인하세요.
4. **로그 확인**: PMS 애플리케이션의 로그를 통해 명령 실행 결과를 확인하세요.

## 문제 해결

### MQTT 연결 문제
```bash
# MQTT 브로커 연결 테스트
mosquitto_pub -h 139.150.70.42 -t "test" -m "hello"
```

### 장비 응답 없음
- 장비 IP 주소 확인
- Modbus 포트 (502) 연결 상태 확인
- 방화벽 설정 확인

### 자동 모드 동작 안함
- `config/config.yml`에서 `auto_mode.enabled: true` 확인
- 필요한 장비 (PCS, BMS) 연결 상태 확인
- SOC 임계값 설정 확인 