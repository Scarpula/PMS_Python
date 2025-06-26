"""
MQTT 클라이언트 모듈
모든 장비 핸들러가 공유하여 사용하는 MQTT 클라이언트
"""

import asyncio
import json
import logging
from typing import Dict, Any
import paho.mqtt.client as mqtt


class MQTTClient:
    """MQTT 클라이언트 클래스"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        MQTT 클라이언트 초기화
        
        Args:
            config: MQTT 설정 딕셔너리
        """
        self.config = config
        self.client = mqtt.Client(client_id=config.get('client_id', 'pms_client'))
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        
        # 구독 중인 토픽 목록 추가
        self.subscribed_topics = set()
        
        # 콜백 설정
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.on_unsubscribe = self._on_unsubscribe
        
        # 사용자 정의 메시지 콜백
        self.message_callback = None
        
        # 자동 재연결 설정
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        
        # 인증 설정
        if config.get('username') and config.get('password'):
            self.client.username_pw_set(config['username'], config['password'])
    
    def _on_connect(self, client, userdata, flags, rc):
        """연결 콜백"""
        if rc == 0:
            self.connected = True
            self.reconnect_attempts = 0  # 재연결 카운터 리셋
            self.logger.info(f"✅ MQTT 브로커에 연결됨: {self.config['broker']}:{self.config['port']}")
            self.logger.info(f"📋 클라이언트 ID: {self.config.get('client_id', 'pms_client')}")
        else:
            self.connected = False
            self.logger.error(f"❌ MQTT 연결 실패, 코드: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """연결 해제 콜백"""
        self.connected = False
        if rc != 0:
            self.logger.warning(f"⚠️ MQTT 연결이 예기치 않게 끊어짐 (코드: {rc})")
            # 자동 재연결 시도
            self._attempt_reconnect()
        else:
            self.logger.info("🔌 MQTT 연결이 정상적으로 종료됨")
    
    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """구독 완료 콜백"""
        self.logger.info(f"✅ 토픽 구독 완료 - MID: {mid}, QoS: {granted_qos}")
    
    def _on_unsubscribe(self, client, userdata, mid):
        """구독 해제 완료 콜백"""
        self.logger.info(f"🔄 토픽 구독 해제 완료 - MID: {mid}")
    
    def _attempt_reconnect(self):
        """재연결 시도"""
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            self.logger.info(f"🔄 MQTT 재연결 시도 {self.reconnect_attempts}/{self.max_reconnect_attempts}")
            try:
                self.client.reconnect()
            except Exception as e:
                self.logger.error(f"❌ 재연결 실패: {e}")
        else:
            self.logger.error(f"💥 최대 재연결 시도 횟수 초과 ({self.max_reconnect_attempts})")
    
    def _on_publish(self, client, userdata, mid):
        """발행 콜백"""
        self.logger.debug(f"📤 메시지 발행 완료, MID: {mid}")
    
    def _on_message(self, client, userdata, msg):
        """메시지 수신 콜백"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            self.logger.info(f"📨 [MQTT 메시지 수신] 토픽: {topic}")
            self.logger.info(f"📄 [MQTT 메시지 내용] {payload}")
            
            if self.message_callback:
                self.logger.debug(f"🔄 메시지 콜백 호출 - 토픽: {topic}")
                # 비동기 콜백을 안전하게 처리
                import asyncio
                import threading
                
                def run_callback():
                    """별도 스레드에서 비동기 콜백 실행"""
                    try:
                        if self.message_callback:
                            # 새 이벤트 루프 생성
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            # 비동기 콜백 실행
                            loop.run_until_complete(self.message_callback(topic, payload))
                            loop.close()
                    except Exception as e:
                        self.logger.error(f"❌ 콜백 실행 중 오류: {e}")
                
                try:
                    # 현재 이벤트 루프가 있는지 확인
                    loop = asyncio.get_running_loop()
                    # 비동기 태스크로 실행
                    asyncio.create_task(self.message_callback(topic, payload))
                    self.logger.debug("✅ 기존 이벤트 루프에서 태스크 생성")
                except RuntimeError:
                    # 이벤트 루프가 없으면 별도 스레드에서 실행
                    self.logger.debug("🔄 별도 스레드에서 메시지 처리")
                    thread = threading.Thread(target=run_callback, daemon=True)
                    thread.start()
            else:
                self.logger.warning(f"⚠️ 메시지 콜백이 설정되지 않음 - 토픽: {topic}")
        except Exception as e:
            self.logger.error(f"❌ 메시지 처리 중 오류: {e}")
    
    async def connect(self):
        """MQTT 브로커에 연결"""
        try:
            self.logger.info(f"🔌 MQTT 브로커 연결 시도: {self.config['broker']}:{self.config['port']}")
            
            # 비동기 연결을 위한 루프
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                self.client.connect, 
                self.config['broker'], 
                self.config['port'], 
                self.config.get('keepalive', 60)
            )
            
            # 백그라운드에서 네트워크 루프 시작
            self.client.loop_start()
            
            # 연결 완료까지 대기
            max_wait = 10  # 최대 10초 대기
            wait_count = 0
            while not self.connected and wait_count < max_wait:
                await asyncio.sleep(1)
                wait_count += 1
            
            if not self.connected:
                raise ConnectionError("MQTT 브로커 연결 시간 초과")
                
        except Exception as e:
            self.logger.error(f"❌ MQTT 연결 실패: {e}")
            raise
    
    async def disconnect(self):
        """MQTT 브로커 연결 해제"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            self.subscribed_topics.clear()
            self.logger.info("🔌 MQTT 연결 해제됨")
    
    def publish(self, topic: str, payload: Dict[str, Any], qos: int = 0, retain: bool = False):
        """
        메시지 발행
        
        Args:
            topic: MQTT 토픽
            payload: 발행할 데이터 (딕셔너리)
            qos: QoS 레벨 (0, 1, 2)
            retain: Retain 플래그
        """
        if not self.connected:
            self.logger.warning("⚠️ MQTT가 연결되지 않음. 메시지 발행 실패")
            return False
        
        try:
            # 딕셔너리를 JSON 문자열로 변환
            json_payload = json.dumps(payload, ensure_ascii=False, default=str)
            
            self.logger.info(f"📤 [MQTT 메시지 발행] 토픽: {topic}")
            self.logger.debug(f"📄 [발행 내용] {json_payload}")
            
            # 메시지 발행
            result = self.client.publish(topic, json_payload, qos, retain)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.info(f"✅ 메시지 발행 완료 - 토픽: {topic}")
                return True
            else:
                self.logger.error(f"❌ 메시지 발행 실패 - 토픽: {topic}, 오류 코드: {result.rc}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ 메시지 발행 중 오류 발생: {e}")
            return False
    
    def is_connected(self) -> bool:
        """연결 상태 확인"""
        return self.connected
    
    def set_message_callback(self, callback):
        """메시지 수신 콜백 설정"""
        self.message_callback = callback
        self.logger.info(f"🔄 MQTT 메시지 콜백 설정됨")
    
    async def subscribe(self, topic: str, qos: int = 0):
        """토픽 구독"""
        if not self.connected:
            self.logger.warning("⚠️ MQTT가 연결되지 않음. 구독 실패")
            return False
        
        try:
            self.logger.info(f"📡 [토픽 구독 시도] {topic}")
            result, mid = self.client.subscribe(topic, qos)
            if result == mqtt.MQTT_ERR_SUCCESS:
                self.subscribed_topics.add(topic)
                self.logger.info(f"✅ 토픽 구독 요청 성공: {topic} (MID: {mid})")
                return True
            else:
                self.logger.error(f"❌ 토픽 구독 실패: {topic}, 오류 코드: {result}")
                return False
        except Exception as e:
            self.logger.error(f"❌ 토픽 구독 중 오류: {e}")
            return False
    
    async def unsubscribe(self, topic: str):
        """토픽 구독 해제"""
        if not self.connected:
            self.logger.warning("⚠️ MQTT가 연결되지 않음. 구독 해제 실패")
            return False
        
        try:
            result, mid = self.client.unsubscribe(topic)
            if result == mqtt.MQTT_ERR_SUCCESS:
                self.subscribed_topics.discard(topic)
                self.logger.info(f"✅ 토픽 구독 해제 완료: {topic}")
                return True
            else:
                self.logger.error(f"❌ 토픽 구독 해제 실패: {topic}, 오류 코드: {result}")
                return False
        except Exception as e:
            self.logger.error(f"❌ 토픽 구독 해제 중 오류: {e}")
            return False 

    def get_subscribed_topics(self) -> set:
        """현재 구독 중인 토픽 목록 반환"""
        return self.subscribed_topics.copy()
    
    def log_status(self):
        """현재 MQTT 클라이언트 상태 로깅"""
        self.logger.info(f"🔍 [MQTT 상태 점검]")
        self.logger.info(f"   📡 연결 상태: {'연결됨' if self.connected else '연결 안됨'}")
        self.logger.info(f"   🏠 브로커: {self.config['broker']}:{self.config['port']}")
        self.logger.info(f"   📋 클라이언트 ID: {self.config.get('client_id', 'pms_client')}")
        self.logger.info(f"   📡 구독 토픽 수: {len(self.subscribed_topics)}")
        if self.subscribed_topics:
            for topic in sorted(self.subscribed_topics):
                self.logger.info(f"      - {topic}")
        self.logger.info(f"   🔄 메시지 콜백: {'설정됨' if self.message_callback else '설정 안됨'}") 