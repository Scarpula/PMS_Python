# 스프링부트 기반 확장성 있는 MQTT 구독 시스템 설계

## 📊 현재 PMS MQTT 토픽 구조

```
토픽 패턴: pms/{device_type}/{device_name}/data
예시:
- pms/PCS/Farm_PCS_01/data
- pms/BMS/Rack1_BMS/data  
- pms/DCDC/Farm_DCDC/data
```

## 🗄️ 데이터베이스 스키마 설계

### 1. Device 테이블
```sql
CREATE TABLE devices (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    device_name VARCHAR(100) NOT NULL UNIQUE,
    device_type ENUM('PCS', 'BMS', 'DCDC') NOT NULL,
    ip_address VARCHAR(45) NOT NULL,
    port INT DEFAULT 502,
    slave_id INT DEFAULT 1,
    poll_interval INT DEFAULT 5,
    site_id BIGINT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_device_type (device_type),
    INDEX idx_site_id (site_id),
    INDEX idx_active (is_active)
);
```

### 2. MQTT Topic Configuration 테이블
```sql
CREATE TABLE mqtt_topic_configs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    topic_pattern VARCHAR(255) NOT NULL,
    description VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 기본 토픽 패턴 삽입
INSERT INTO mqtt_topic_configs (topic_pattern, description) VALUES 
('pms/{device_type}/{device_name}/data', 'PMS 장비 데이터 토픽'),
('pms/{device_type}/{device_name}/status', 'PMS 장비 상태 토픽'),
('pms/{device_type}/{device_name}/alarm', 'PMS 장비 알람 토픽');
```

### 3. Device Data 테이블 (시계열 데이터)
```sql
CREATE TABLE device_data (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    device_id BIGINT NOT NULL,
    topic VARCHAR(255) NOT NULL,
    raw_data JSON,
    processed_data JSON,
    timestamp TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),
    
    FOREIGN KEY (device_id) REFERENCES devices(id),
    INDEX idx_device_timestamp (device_id, timestamp),
    INDEX idx_topic (topic)
);
```

### 4. Site 테이블 (다중 사이트 지원)
```sql
CREATE TABLE sites (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    site_name VARCHAR(100) NOT NULL UNIQUE,
    site_code VARCHAR(20) NOT NULL UNIQUE,
    location VARCHAR(255),
    timezone VARCHAR(50) DEFAULT 'Asia/Seoul',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 🚀 스프링부트 구현

### 1. Entity 클래스

```java
@Entity
@Table(name = "devices")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class Device {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    @Column(name = "device_name", unique = true, nullable = false)
    private String deviceName;
    
    @Enumerated(EnumType.STRING)
    @Column(name = "device_type", nullable = false)
    private DeviceType deviceType;
    
    @Column(name = "ip_address", nullable = false)
    private String ipAddress;
    
    @Column(name = "port")
    private Integer port = 502;
    
    @Column(name = "slave_id")
    private Integer slaveId = 1;
    
    @Column(name = "poll_interval")
    private Integer pollInterval = 5;
    
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "site_id")
    private Site site;
    
    @Column(name = "is_active")
    private Boolean isActive = true;
    
    @CreationTimestamp
    @Column(name = "created_at")
    private LocalDateTime createdAt;
    
    @UpdateTimestamp
    @Column(name = "updated_at")
    private LocalDateTime updatedAt;
}

public enum DeviceType {
    PCS, BMS, DCDC
}
```

### 2. MQTT 토픽 생성 서비스

```java
@Service
@Slf4j
public class MqttTopicService {
    
    @Autowired
    private DeviceRepository deviceRepository;
    
    @Autowired
    private MqttTopicConfigRepository topicConfigRepository;
    
    /**
     * 장비별 구독 토픽 목록 생성
     */
    public List<String> generateSubscriptionTopics() {
        List<Device> activeDevices = deviceRepository.findByIsActiveTrue();
        List<MqttTopicConfig> activeConfigs = topicConfigRepository.findByIsActiveTrue();
        
        List<String> topics = new ArrayList<>();
        
        for (Device device : activeDevices) {
            for (MqttTopicConfig config : activeConfigs) {
                String topic = buildTopic(config.getTopicPattern(), device);
                topics.add(topic);
            }
        }
        
        return topics;
    }
    
    /**
     * 토픽 패턴에서 실제 토픽 생성
     */
    private String buildTopic(String pattern, Device device) {
        return pattern
            .replace("{device_type}", device.getDeviceType().name())
            .replace("{device_name}", device.getDeviceName())
            .replace("{site_code}", device.getSite() != null ? device.getSite().getSiteCode() : "default");
    }
    
    /**
     * 와일드카드 토픽 생성 (모든 장비 구독)
     */
    public List<String> generateWildcardTopics() {
        return Arrays.asList(
            "pms/+/+/data",      // 모든 장비의 데이터
            "pms/+/+/status",    // 모든 장비의 상태
            "pms/+/+/alarm"      // 모든 장비의 알람
        );
    }
    
    /**
     * 특정 장비 타입별 토픽 생성
     */
    public List<String> generateTopicsByDeviceType(DeviceType deviceType) {
        return Arrays.asList(
            String.format("pms/%s/+/data", deviceType.name()),
            String.format("pms/%s/+/status", deviceType.name()),
            String.format("pms/%s/+/alarm", deviceType.name())
        );
    }
}
```

### 3. 동적 MQTT 구독 관리자

```java
@Component
@Slf4j
public class DynamicMqttSubscriptionManager {
    
    @Autowired
    private MqttTopicService mqttTopicService;
    
    @Autowired
    private IMqttClient mqttClient;
    
    private final Set<String> currentSubscriptions = ConcurrentHashMap.newKeySet();
    
    /**
     * 장비 목록 변경 시 구독 토픽 업데이트
     */
    @EventListener
    public void handleDeviceChange(DeviceChangeEvent event) {
        log.info("장비 변경 감지: {}", event.getDeviceName());
        updateSubscriptions();
    }
    
    /**
     * 구독 토픽 업데이트
     */
    @Scheduled(fixedRate = 60000) // 1분마다 체크
    public void updateSubscriptions() {
        try {
            List<String> newTopics = mqttTopicService.generateSubscriptionTopics();
            
            // 새로운 토픽 구독
            for (String topic : newTopics) {
                if (!currentSubscriptions.contains(topic)) {
                    mqttClient.subscribe(topic, 1);
                    currentSubscriptions.add(topic);
                    log.info("새 토픽 구독: {}", topic);
                }
            }
            
            // 더 이상 필요없는 토픽 구독 해제
            Set<String> topicsToRemove = new HashSet<>(currentSubscriptions);
            topicsToRemove.removeAll(newTopics);
            
            for (String topic : topicsToRemove) {
                mqttClient.unsubscribe(topic);
                currentSubscriptions.remove(topic);
                log.info("토픽 구독 해제: {}", topic);
            }
            
        } catch (Exception e) {
            log.error("구독 토픽 업데이트 실패", e);
        }
    }
    
    /**
     * 초기 구독 설정
     */
    @PostConstruct
    public void initializeSubscriptions() {
        // 와일드카드 토픽으로 시작 (모든 메시지 수신)
        List<String> wildcardTopics = mqttTopicService.generateWildcardTopics();
        
        for (String topic : wildcardTopics) {
            try {
                mqttClient.subscribe(topic, 1);
                currentSubscriptions.add(topic);
                log.info("초기 토픽 구독: {}", topic);
            } catch (Exception e) {
                log.error("초기 토픽 구독 실패: {}", topic, e);
            }
        }
    }
}
```

### 4. MQTT 메시지 처리기

```java
@Component
@Slf4j
public class MqttMessageProcessor {
    
    @Autowired
    private DeviceRepository deviceRepository;
    
    @Autowired
    private DeviceDataService deviceDataService;
    
    @Autowired
    private ObjectMapper objectMapper;
    
    /**
     * MQTT 메시지 처리
     */
    @MqttMessageHandler
    public void handleMessage(String topic, String payload) {
        try {
            // 토픽에서 장비 정보 추출
            DeviceInfo deviceInfo = parseTopicForDeviceInfo(topic);
            if (deviceInfo == null) {
                log.warn("토픽에서 장비 정보 추출 실패: {}", topic);
                return;
            }
            
            // 장비 조회
            Optional<Device> deviceOpt = deviceRepository.findByDeviceName(deviceInfo.getDeviceName());
            if (!deviceOpt.isPresent()) {
                log.warn("알 수 없는 장비: {}", deviceInfo.getDeviceName());
                return;
            }
            
            Device device = deviceOpt.get();
            
            // 메시지 타입별 처리
            switch (deviceInfo.getMessageType()) {
                case "data":
                    handleDataMessage(device, topic, payload);
                    break;
                case "status":
                    handleStatusMessage(device, topic, payload);
                    break;
                case "alarm":
                    handleAlarmMessage(device, topic, payload);
                    break;
                default:
                    log.warn("알 수 없는 메시지 타입: {}", deviceInfo.getMessageType());
            }
            
        } catch (Exception e) {
            log.error("MQTT 메시지 처리 실패 - Topic: {}, Payload: {}", topic, payload, e);
        }
    }
    
    /**
     * 데이터 메시지 처리
     */
    private void handleDataMessage(Device device, String topic, String payload) {
        try {
            JsonNode jsonData = objectMapper.readTree(payload);
            
            // 비트마스크 데이터 특별 처리
            if (jsonData.has("data")) {
                JsonNode dataNode = jsonData.get("data");
                processBitmaskData(device, dataNode);
            }
            
            // 데이터베이스 저장
            deviceDataService.saveDeviceData(device, topic, payload);
            
            // 실시간 알림 (WebSocket 등)
            sendRealTimeUpdate(device, jsonData);
            
        } catch (Exception e) {
            log.error("데이터 메시지 처리 실패", e);
        }
    }
    
    /**
     * 비트마스크 데이터 처리
     */
    private void processBitmaskData(Device device, JsonNode dataNode) {
        dataNode.fields().forEachRemaining(entry -> {
            String key = entry.getKey();
            JsonNode value = entry.getValue();
            
            if (value.has("type") && "bitmask".equals(value.get("type").asText())) {
                processBitmaskField(device, key, value);
            }
        });
    }
    
    /**
     * 개별 비트마스크 필드 처리
     */
    private void processBitmaskField(Device device, String fieldName, JsonNode bitmaskData) {
        // additional_status에서 중요한 상태 정보 추출
        if (bitmaskData.has("additional_status")) {
            JsonNode additionalStatus = bitmaskData.get("additional_status");
            
            // 운전 모드 변경 감지
            if (additionalStatus.has("operating_mode")) {
                String operatingMode = additionalStatus.get("operating_mode").get("text").asText();
                handleOperatingModeChange(device, operatingMode);
            }
            
            // 고장 상태 감지
            if (additionalStatus.has("fault_status")) {
                int faultCode = additionalStatus.get("fault_status").get("code").asInt();
                if (faultCode == 1) {
                    handleFaultDetection(device, fieldName);
                }
            }
        }
    }
    
    /**
     * 토픽에서 장비 정보 추출
     */
    private DeviceInfo parseTopicForDeviceInfo(String topic) {
        // 토픽 패턴: pms/{device_type}/{device_name}/{message_type}
        String[] parts = topic.split("/");
        if (parts.length >= 4) {
            return DeviceInfo.builder()
                .deviceType(parts[1])
                .deviceName(parts[2])
                .messageType(parts[3])
                .build();
        }
        return null;
    }
    
    @Data
    @Builder
    private static class DeviceInfo {
        private String deviceType;
        private String deviceName;
        private String messageType;
    }
}
```

### 5. REST API 컨트롤러

```java
@RestController
@RequestMapping("/api/mqtt")
@Slf4j
public class MqttManagementController {
    
    @Autowired
    private MqttTopicService mqttTopicService;
    
    @Autowired
    private DynamicMqttSubscriptionManager subscriptionManager;
    
    @Autowired
    private DeviceService deviceService;
    
    /**
     * 현재 구독 중인 토픽 목록 조회
     */
    @GetMapping("/subscriptions")
    public ResponseEntity<List<String>> getCurrentSubscriptions() {
        List<String> topics = mqttTopicService.generateSubscriptionTopics();
        return ResponseEntity.ok(topics);
    }
    
    /**
     * 장비별 토픽 조회
     */
    @GetMapping("/topics/device/{deviceName}")
    public ResponseEntity<List<String>> getTopicsByDevice(@PathVariable String deviceName) {
        Optional<Device> device = deviceService.findByDeviceName(deviceName);
        if (!device.isPresent()) {
            return ResponseEntity.notFound().build();
        }
        
        List<String> topics = mqttTopicService.generateTopicsForDevice(device.get());
        return ResponseEntity.ok(topics);
    }
    
    /**
     * 구독 토픽 강제 업데이트
     */
    @PostMapping("/subscriptions/refresh")
    public ResponseEntity<String> refreshSubscriptions() {
        subscriptionManager.updateSubscriptions();
        return ResponseEntity.ok("구독 토픽이 업데이트되었습니다.");
    }
    
    /**
     * 장비 타입별 토픽 조회
     */
    @GetMapping("/topics/type/{deviceType}")
    public ResponseEntity<List<String>> getTopicsByDeviceType(@PathVariable DeviceType deviceType) {
        List<String> topics = mqttTopicService.generateTopicsByDeviceType(deviceType);
        return ResponseEntity.ok(topics);
    }
}
```

## 🔧 설정 파일

### application.yml
```yaml
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/pms_db
    username: ${DB_USERNAME:pms_user}
    password: ${DB_PASSWORD:pms_password}
    
  jpa:
    hibernate:
      ddl-auto: validate
    show-sql: false
    properties:
      hibernate:
        format_sql: true
        
mqtt:
  broker:
    url: tcp://localhost:1883
    username: ${MQTT_USERNAME:}
    password: ${MQTT_PASSWORD:}
    client-id: pms-backend-${random.uuid}
    
  subscription:
    qos: 1
    auto-startup: true
    
logging:
  level:
    com.pms.mqtt: DEBUG
```

## 🚀 주요 특징

### 1. **동적 구독 관리**
- DB에서 장비 목록을 읽어와 자동으로 토픽 구독
- 장비 추가/삭제 시 자동으로 구독 토픽 업데이트
- 와일드카드 토픽 지원으로 유연한 구독

### 2. **확장성**
- 새로운 장비 타입 추가 시 코드 변경 최소화
- 토픽 패턴을 DB에서 관리하여 유연한 변경 가능
- 다중 사이트 지원

### 3. **실시간 처리**
- 비트마스크 데이터의 상태 변화 실시간 감지
- 고장/알람 상태 즉시 처리
- WebSocket을 통한 실시간 알림

### 4. **모니터링 및 관리**
- REST API를 통한 구독 상태 모니터링
- 구독 토픽 강제 업데이트 기능
- 장비별/타입별 토픽 조회

이 설계를 통해 PMS 시스템의 장비가 추가되거나 변경되어도 백엔드에서 자동으로 감지하고 적절한 MQTT 토픽을 구독할 수 있습니다. 