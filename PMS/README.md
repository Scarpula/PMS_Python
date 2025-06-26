# PMS (Power Management System)

스마트 팜을 위한 전력 관리 시스템입니다. BMS, PCS, DCDC 등의 장비를 통합 관리하고, 실시간 모니터링 및 제어 기능을 제공합니다.

## 주요 기능

### 1. 실시간 데이터 수집 및 모니터링
- BMS: 배터리 상태 (SOC, 전압, 전류, 온도)
- PCS: 인버터 상태 (AC/DC 전력, 전압, 주파수)
- DCDC: 태양광 발전 상태 (입출력 전압/전류, 전력)

### 2. MQTT 기반 통신
- 실시간 데이터 발행
- 원격 제어 명령 수신
- 상태 정보 및 알람 전송

### 3. 운전 모드 관리
#### 기본 운전 모드 (Basic Mode)
- 개별 장비 수동 제어
- 실시간 모니터링
- MQTT 토픽: `pms/control/basic_mode`

#### 자동 운전 모드 (Auto Mode)  
- 지능형 자동 제어 시스템
- SOC 기반 자동 충방전 제어
- 태양광 발전 최적화

### 4. 자동 운전 모드 상세 기능

#### 시작 시퀀스
1. **PCS 대기 모드**: PCS Standby (21) → 5초 대기 → 독립운전 (24)
2. **DCDC 구동**: DCDC Reset (100) → 5초 대기 → Solar 발전 (107)

#### SOC 기반 자동 제어
- **SOC 상한 (88% 이상)**: 
  - DCDC 대기 모드 (106) 전환
  - 설정 시간 경과 후 Solar 발전 (107) 복귀
  
- **SOC 하한 (5% 이하)**:
  - PCS Stop (20) → 5초 → PCS RUN (21) → 5초 → BAT 충전 (22)
  - 충전 정지 SOC 도달 시 → PCS Stop (20) → 독립운전 (24)

#### 자동 모드 제어 토픽
```bash
# 자동 모드로 전환
pms/control/operation_mode
{"mode": "auto"}

# 자동 모드 시작
pms/control/auto_mode/start
{}

# 자동 모드 정지
pms/control/auto_mode/stop
{}

# 상태 조회
pms/control/auto_mode/status
{}
```

## 설치 및 실행

### 1. 의존성 설치
```bash
pip install -r requirements.txt
```

### 2. 설정 파일 수정
`config/config.yml` 파일에서 장비 정보 및 MQTT 설정을 수정하세요.

### 3. 실행
```bash
# 일반 실행
python main.py

# GUI 실행
python main_gui_integrated.py
```

## 설정 파일 구조

### 기본 설정 (config/config.yml)
```yaml
# MQTT 브로커 설정
mqtt:
  broker: "139.150.70.42"
  port: 1883
  base_topic: "pms"

# 장비 목록
devices:
  - name: "한자연_BMS"
    type: "BMS"
    ip: "192.168.1.60"
    port: 502
    slave_id: 1
    poll_interval: 2

# 자동 운전 모드 설정
auto_mode:
  enabled: true
  soc_high_threshold: 88.0        # SOC 상한 임계값 (%)
  soc_low_threshold: 5.0          # SOC 하한 임계값 (%)
  soc_charge_stop_threshold: 25.0 # 충전 정지 SOC (%)
  dcdc_standby_time: 30           # DCDC 대기 시간 (초)
  command_interval: 5             # 명령 간격 (초)
  charging_power: 10.0            # 충전 전력 (kW)
```

## MQTT 토픽 구조

### 데이터 발행 토픽
- `pms/BMS/{device_name}/data` - BMS 데이터
- `pms/PCS/{device_name}/data` - PCS 데이터  
- `pms/DCDC/{device_name}/data` - DCDC 데이터

### 제어 토픽
- `pms/control/operation_mode` - 운전 모드 전환
- `pms/control/auto_mode/start` - 자동 모드 시작
- `pms/control/auto_mode/stop` - 자동 모드 정지
- `pms/control/basic_mode` - 기본 모드 제어
- `pms/status/operation_mode` - 운전 모드 상태

### 상태 응답 토픽
- `pms/status/operation_mode/response` - 제어 명령 응답

## 프로젝트 구조

```
PMS/
├── config/                     # 설정 파일
│   ├── config.yml             # 기본 설정
│   ├── bms_map.json          # BMS 메모리 맵
│   ├── pcs_map.json          # PCS 메모리 맵
│   └── dcdc_map.json         # DCDC 메모리 맵
├── pms_app/                   # 메인 애플리케이션
│   ├── core/                  # 핵심 모듈
│   │   ├── scheduler.py       # 스케줄러
│   │   └── mqtt_client.py     # MQTT 클라이언트
│   ├── devices/              # 장비 핸들러
│   │   ├── base.py           # 기본 인터페이스
│   │   ├── bms_handler.py    # BMS 핸들러
│   │   ├── pcs_handler.py    # PCS 핸들러
│   │   └── dcdc_handler.py   # DCDC 핸들러
│   ├── automation/           # 자동화 모듈 (신규)
│   │   ├── operation_manager.py # 운전 모드 관리자
│   │   ├── auto_mode.py      # 자동 운전 제어기
│   │   └── state_machine.py  # 자동 모드 상태 머신
│   ├── gui/                  # GUI 모듈
│   └── utils/                # 유틸리티
├── main.py                   # 메인 실행 파일
├── main_gui_integrated.py    # GUI 통합 실행 파일
└── README.md
```

## 빌드 (실행 파일 생성)

### 일반 빌드 (모든 장비 포함)
```bash
.\build_normal.bat
```

### DCDC 비활성화 빌드
```bash
.\build_dcdc_disabled.bat
```

생성된 실행 파일: `dist\PMS_GUI_Application.exe`

## 개발 정보

- **언어**: Python 3.8+
- **주요 라이브러리**: asyncio, pymodbus, paho-mqtt, PyQt5
- **통신 프로토콜**: Modbus TCP, MQTT
- **아키텍처**: 비동기 이벤트 기반, 팩토리 패턴

## 라이센스

이 프로젝트는 내부 사용을 위한 것입니다. 