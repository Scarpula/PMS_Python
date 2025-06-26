# 스프링부트 MQTT 구독 시스템 실제 구현 예제

## 📦 Maven Dependencies

```xml
<dependencies>
    <!-- Spring Boot Starters -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-websocket</artifactId>
    </dependency>
    
    <!-- MQTT -->
    <dependency>
        <groupId>org.springframework.integration</groupId>
        <artifactId>spring-integration-mqtt</artifactId>
    </dependency>
    <dependency>
        <groupId>org.eclipse.paho</groupId>
        <artifactId>org.eclipse.paho.client.mqttv3</artifactId>
        <version>1.2.5</version>
    </dependency>
    
    <!-- Database -->
    <dependency>
        <groupId>mysql</groupId>
        <artifactId>mysql-connector-java</artifactId>
    </dependency>
    
    <!-- JSON Processing -->
    <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
    </dependency>
    
    <!-- Lombok -->
    <dependency>
        <groupId>org.projectlombok</groupId>
        <artifactId>lombok</artifactId>
        <optional>true</optional>
    </dependency>
</dependencies>
```

## 🔧 MQTT 설정 클래스

```java
@Configuration
@EnableConfigurationProperties(MqttProperties.class)
@Slf4j
public class MqttConfig {
    
    @Autowired
    private MqttProperties mqttProperties;
    
    @Bean
    public MqttConnectOptions mqttConnectOptions() {
        MqttConnectOptions options = new MqttConnectOptions();
        options.setServerURIs(new String[]{mqttProperties.getBroker().getUrl()});
        options.setCleanSession(true);
        options.setKeepAliveInterval(60);
        options.setConnectionTimeout(30);
        options.setAutomaticReconnect(true);
        
        if (mqttProperties.getBroker().getUsername() != null) {
            options.setUserName(mqttProperties.getBroker().getUsername());
            options.setPassword(mqttProperties.getBroker().getPassword().toCharArray());
        }
        
        return options;
    }
    
    @Bean
    public MqttClientFactory mqttClientFactory() {
        DefaultMqttPahoClientFactory factory = new DefaultMqttPahoClientFactory();
        factory.setConnectionOptions(mqttConnectOptions());
        return factory;
    }
    
    @Bean
    public MessageProducer mqttInbound() {
        MqttPahoMessageDrivenChannelAdapter adapter = 
            new MqttPahoMessageDrivenChannelAdapter(
                mqttProperties.getBroker().getClientId(),
                mqttClientFactory(),
                "pms/+/+/+"  // 초기 와일드카드 구독
            );
        
        adapter.setCompletionTimeout(5000);
        adapter.setConverter(new DefaultPahoMessageConverter());
        adapter.setQos(mqttProperties.getSubscription().getQos());
        adapter.setOutputChannel(mqttInputChannel());
        
        return adapter;
    }
    
    @Bean
    public MessageChannel mqttInputChannel() {
        return new DirectChannel();
    }
    
    @Bean
    @ServiceActivator(inputChannel = "mqttInputChannel")
    public MessageHandler mqttMessageHandler() {
        return new MqttMessageHandler();
    }
}

@ConfigurationProperties(prefix = "mqtt")
@Data
public class MqttProperties {
    private Broker broker = new Broker();
    private Subscription subscription = new Subscription();
    
    @Data
    public static class Broker {
        private String url = "tcp://localhost:1883";
        private String username;
        private String password;
        private String clientId = "pms-backend";
    }
    
    @Data
    public static class Subscription {
        private int qos = 1;
        private boolean autoStartup = true;
    }
}
```

## 📨 MQTT 메시지 핸들러

```java
@Component
@Slf4j
public class MqttMessageHandler implements MessageHandler {
    
    @Autowired
    private MqttMessageProcessor messageProcessor;
    
    @Override
    public void handleMessage(Message<?> message) throws MessagingException {
        try {
            String topic = (String) message.getHeaders().get("mqtt_receivedTopic");
            String payload = (String) message.getPayload();
            
            log.debug("MQTT 메시지 수신 - Topic: {}, Payload length: {}", topic, payload.length());
            
            // 비동기로 메시지 처리
            CompletableFuture.runAsync(() -> {
                try {
                    messageProcessor.processMessage(topic, payload);
                } catch (Exception e) {
                    log.error("메시지 처리 중 오류 발생", e);
                }
            });
            
        } catch (Exception e) {
            log.error("MQTT 메시지 핸들링 실패", e);
        }
    }
}
```

## 🔄 동적 구독 관리 서비스

```java
@Service
@Slf4j
public class DynamicMqttSubscriptionService {
    
    @Autowired
    private MqttTopicService topicService;
    
    @Autowired
    private MqttPahoMessageDrivenChannelAdapter mqttAdapter;
    
    private final Set<String> activeSubscriptions = ConcurrentHashMap.newKeySet();
    
    @PostConstruct
    public void initializeSubscriptions() {
        // 애플리케이션 시작 시 DB에서 장비 목록을 읽어와 구독 설정
        updateSubscriptionsFromDatabase();
    }
    
    @Scheduled(fixedRate = 300000) // 5분마다 체크
    public void periodicSubscriptionUpdate() {
        updateSubscriptionsFromDatabase();
    }
    
    @EventListener
    public void handleDeviceChangeEvent(DeviceChangeEvent event) {
        log.info("장비 변경 이벤트 감지: {} - {}", event.getEventType(), event.getDeviceName());
        updateSubscriptionsFromDatabase();
    }
    
    public void updateSubscriptionsFromDatabase() {
        try {
            List<String> requiredTopics = topicService.generateAllRequiredTopics();
            
            // 새로운 토픽 구독
            Set<String> newTopics = new HashSet<>(requiredTopics);
            newTopics.removeAll(activeSubscriptions);
            
            for (String topic : newTopics) {
                subscribeToTopic(topic);
            }
            
            // 불필요한 토픽 구독 해제
            Set<String> obsoleteTopics = new HashSet<>(activeSubscriptions);
            obsoleteTopics.removeAll(requiredTopics);
            
            for (String topic : obsoleteTopics) {
                unsubscribeFromTopic(topic);
            }
            
            log.info("구독 토픽 업데이트 완료 - 활성: {}, 추가: {}, 제거: {}", 
                    activeSubscriptions.size(), newTopics.size(), obsoleteTopics.size());
            
        } catch (Exception e) {
            log.error("구독 토픽 업데이트 실패", e);
        }
    }
    
    private void subscribeToTopic(String topic) {
        try {
            mqttAdapter.addTopic(topic, 1);
            activeSubscriptions.add(topic);
            log.info("토픽 구독 추가: {}", topic);
        } catch (Exception e) {
            log.error("토픽 구독 실패: {}", topic, e);
        }
    }
    
    private void unsubscribeFromTopic(String topic) {
        try {
            mqttAdapter.removeTopic(topic);
            activeSubscriptions.remove(topic);
            log.info("토픽 구독 해제: {}", topic);
        } catch (Exception e) {
            log.error("토픽 구독 해제 실패: {}", topic, e);
        }
    }
    
    public Set<String> getActiveSubscriptions() {
        return new HashSet<>(activeSubscriptions);
    }
}
```

## 📊 비트마스크 데이터 처리 서비스

```java
@Service
@Slf4j
public class BitmaskDataProcessor {
    
    @Autowired
    private DeviceAlarmService alarmService;
    
    @Autowired
    private DeviceStatusService statusService;
    
    @Autowired
    private WebSocketNotificationService notificationService;
    
    /**
     * 비트마스크 데이터에서 중요한 상태 변화 감지 및 처리
     */
    public void processBitmaskData(Device device, JsonNode dataNode) {
        dataNode.fields().forEachRemaining(entry -> {
            String fieldName = entry.getKey();
            JsonNode fieldValue = entry.getValue();
            
            if (isBitmaskField(fieldValue)) {
                processBitmaskField(device, fieldName, fieldValue);
            }
        });
    }
    
    private boolean isBitmaskField(JsonNode fieldValue) {
        return fieldValue.has("type") && "bitmask".equals(fieldValue.get("type").asText());
    }
    
    private void processBitmaskField(Device device, String fieldName, JsonNode bitmaskData) {
        try {
            // additional_status에서 중요한 정보 추출
            if (bitmaskData.has("additional_status")) {
                JsonNode additionalStatus = bitmaskData.get("additional_status");
                
                // 운전 모드 변경 처리
                processOperatingModeChange(device, additionalStatus);
                
                // 고장 상태 처리
                processFaultStatus(device, fieldName, additionalStatus);
                
                // 알람 상태 처리
                processAlarmStatus(device, fieldName, additionalStatus);
                
                // 제어 모드 변경 처리
                processControlModeChange(device, additionalStatus);
            }
            
            // status_values에서 개별 비트 상태 처리
            if (bitmaskData.has("status_values")) {
                JsonNode statusValues = bitmaskData.get("status_values");
                processIndividualBitStatus(device, fieldName, statusValues);
            }
            
        } catch (Exception e) {
            log.error("비트마스크 데이터 처리 중 오류 - Device: {}, Field: {}", 
                    device.getDeviceName(), fieldName, e);
        }
    }
    
    private void processOperatingModeChange(Device device, JsonNode additionalStatus) {
        if (additionalStatus.has("operating_mode")) {
            JsonNode operatingMode = additionalStatus.get("operating_mode");
            String currentMode = operatingMode.get("text").asText();
            int modeCode = operatingMode.get("code").asInt();
            
            // 이전 모드와 비교하여 변경 감지
            String previousMode = statusService.getLastOperatingMode(device.getId());
            
            if (!currentMode.equals(previousMode)) {
                log.info("장비 운전 모드 변경 - Device: {}, {} -> {}", 
                        device.getDeviceName(), previousMode, currentMode);
                
                // 상태 업데이트
                statusService.updateOperatingMode(device.getId(), currentMode, modeCode);
                
                // 실시간 알림
                notificationService.sendOperatingModeChange(device, previousMode, currentMode);
                
                // 특정 모드 변경에 대한 추가 처리
                handleSpecificModeChange(device, currentMode, modeCode);
            }
        }
    }
    
    private void processFaultStatus(Device device, String fieldName, JsonNode additionalStatus) {
        if (additionalStatus.has("fault_status")) {
            JsonNode faultStatus = additionalStatus.get("fault_status");
            int faultCode = faultStatus.get("code").asInt();
            String faultText = faultStatus.get("text").asText();
            
            if (faultCode == 1) { // 고장 발생
                log.warn("장비 고장 감지 - Device: {}, Field: {}, Status: {}", 
                        device.getDeviceName(), fieldName, faultText);
                
                // 알람 생성
                alarmService.createFaultAlarm(device, fieldName, faultText);
                
                // 긴급 알림
                notificationService.sendUrgentAlert(device, "고장 발생", faultText);
                
            } else { // 고장 해제
                // 기존 알람 해제
                alarmService.resolveFaultAlarm(device, fieldName);
            }
        }
    }
    
    private void processAlarmStatus(Device device, String fieldName, JsonNode additionalStatus) {
        // 화재 경보 처리 (BMS)
        if (additionalStatus.has("fire_alarm")) {
            JsonNode fireAlarm = additionalStatus.get("fire_alarm");
            int alarmCode = fireAlarm.get("code").asInt();
            
            if (alarmCode == 1) {
                log.error("화재 경보 발생 - Device: {}", device.getDeviceName());
                alarmService.createFireAlarm(device);
                notificationService.sendEmergencyAlert(device, "화재 경보", "즉시 대응 필요");
            }
        }
        
        // 연기 센서 처리 (BMS)
        if (additionalStatus.has("smoke_sensor")) {
            JsonNode smokeSensor = additionalStatus.get("smoke_sensor");
            int sensorCode = smokeSensor.get("code").asInt();
            
            if (sensorCode == 1) {
                log.warn("연기 센서 고장 - Device: {}", device.getDeviceName());
                alarmService.createSensorFaultAlarm(device, "연기 센서");
            }
        }
    }
    
    private void processControlModeChange(Device device, JsonNode additionalStatus) {
        if (additionalStatus.has("control_mode")) {
            JsonNode controlMode = additionalStatus.get("control_mode");
            String currentMode = controlMode.get("text").asText();
            
            // 제어 모드 변경 로그
            log.info("장비 제어 모드: {} - {}", device.getDeviceName(), currentMode);
            
            // 원격 제어 모드로 변경 시 보안 로그
            if ("원격 제어".equals(currentMode)) {
                log.info("원격 제어 모드 활성화 - Device: {}", device.getDeviceName());
                // 보안 감사 로그 생성
            }
        }
    }
    
    private void processIndividualBitStatus(Device device, String fieldName, JsonNode statusValues) {
        statusValues.fields().forEachRemaining(entry -> {
            String bitKey = entry.getKey();
            JsonNode bitStatus = entry.getValue();
            
            String status = bitStatus.get("status").asText();
            
            // 특정 비트 상태에 대한 처리
            if (status.contains("이상") || status.contains("고장") || status.contains("경고")) {
                log.warn("비트 상태 이상 - Device: {}, Field: {}, Bit: {}, Status: {}", 
                        device.getDeviceName(), fieldName, bitKey, status);
                
                // 세부 알람 생성
                alarmService.createBitStatusAlarm(device, fieldName, bitKey, status);
            }
        });
    }
    
    private void handleSpecificModeChange(Device device, String mode, int modeCode) {
        switch (mode) {
            case "정지":
                // 정지 모드 진입 시 추가 체크
                log.info("장비 정지 모드 진입 - Device: {}", device.getDeviceName());
                break;
                
            case "고장 발생":
                // 고장 모드 진입 시 긴급 처리
                log.error("장비 고장 모드 진입 - Device: {}", device.getDeviceName());
                notificationService.sendEmergencyAlert(device, "고장 모드", "즉시 점검 필요");
                break;
                
            case "충전":
            case "방전":
                // 충전/방전 모드 진입 시 성능 모니터링 시작
                log.info("장비 {}모드 진입 - Device: {}", mode, device.getDeviceName());
                break;
        }
    }
}
```

## 🔔 실시간 알림 서비스

```java
@Service
@Slf4j
public class WebSocketNotificationService {
    
    @Autowired
    private SimpMessagingTemplate messagingTemplate;
    
    public void sendOperatingModeChange(Device device, String previousMode, String currentMode) {
        Map<String, Object> notification = Map.of(
            "type", "OPERATING_MODE_CHANGE",
            "deviceName", device.getDeviceName(),
            "deviceType", device.getDeviceType().name(),
            "previousMode", previousMode,
            "currentMode", currentMode,
            "timestamp", Instant.now().toString()
        );
        
        messagingTemplate.convertAndSend("/topic/device-status", notification);
        log.debug("운전 모드 변경 알림 전송: {}", device.getDeviceName());
    }
    
    public void sendUrgentAlert(Device device, String alertType, String message) {
        Map<String, Object> alert = Map.of(
            "type", "URGENT_ALERT",
            "severity", "HIGH",
            "deviceName", device.getDeviceName(),
            "deviceType", device.getDeviceType().name(),
            "alertType", alertType,
            "message", message,
            "timestamp", Instant.now().toString()
        );
        
        messagingTemplate.convertAndSend("/topic/urgent-alerts", alert);
        log.warn("긴급 알림 전송: {} - {}", device.getDeviceName(), alertType);
    }
    
    public void sendEmergencyAlert(Device device, String alertType, String message) {
        Map<String, Object> alert = Map.of(
            "type", "EMERGENCY_ALERT",
            "severity", "CRITICAL",
            "deviceName", device.getDeviceName(),
            "deviceType", device.getDeviceType().name(),
            "alertType", alertType,
            "message", message,
            "timestamp", Instant.now().toString()
        );
        
        messagingTemplate.convertAndSend("/topic/emergency-alerts", alert);
        log.error("비상 알림 전송: {} - {}", device.getDeviceName(), alertType);
    }
}
```

## 🎯 사용 예시

### 1. 새 장비 추가 시
```java
@RestController
@RequestMapping("/api/devices")
public class DeviceController {
    
    @Autowired
    private DeviceService deviceService;
    
    @Autowired
    private ApplicationEventPublisher eventPublisher;
    
    @PostMapping
    public ResponseEntity<Device> createDevice(@RequestBody DeviceCreateRequest request) {
        Device device = deviceService.createDevice(request);
        
        // 장비 변경 이벤트 발행 (자동으로 MQTT 구독 업데이트)
        eventPublisher.publishEvent(new DeviceChangeEvent(
            DeviceChangeEvent.EventType.CREATED, 
            device.getDeviceName()
        ));
        
        return ResponseEntity.ok(device);
    }
}
```

### 2. 실시간 모니터링 대시보드
```javascript
// WebSocket 연결
const socket = new SockJS('/ws');
const stompClient = Stomp.over(socket);

stompClient.connect({}, function(frame) {
    // 장비 상태 변경 구독
    stompClient.subscribe('/topic/device-status', function(message) {
        const data = JSON.parse(message.body);
        updateDeviceStatus(data);
    });
    
    // 긴급 알림 구독
    stompClient.subscribe('/topic/urgent-alerts', function(message) {
        const alert = JSON.parse(message.body);
        showUrgentAlert(alert);
    });
    
    // 비상 알림 구독
    stompClient.subscribe('/topic/emergency-alerts', function(message) {
        const alert = JSON.parse(message.body);
        showEmergencyAlert(alert);
    });
});
```

이 구현을 통해 PMS 시스템의 장비가 동적으로 추가/제거되어도 스프링부트 백엔드에서 자동으로 적절한 MQTT 토픽을 구독하고, 비트마스크 데이터의 상태 변화를 실시간으로 감지하여 적절한 조치를 취할 수 있습니다. 