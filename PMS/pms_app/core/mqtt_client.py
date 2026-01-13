"""
MQTT 클라이언트 모듈
모든 장비 핸들러가 공유하여 사용하는 MQTT 클라이언트
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional
import paho.mqtt.client as mqtt
import time
import uuid
from datetime import datetime
import threading
import inspect  # 추가: 함수 타입 검사용
from queue import Queue, Empty
import concurrent.futures
from dataclasses import dataclass


@dataclass
class MQTTMessage:
    """MQTT 메시지 데이터 클래스"""
    topic: str
    payload: Dict[str, Any]
    qos: int = 0
    retain: bool = False
    timestamp: Optional[float] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class MQTTPublisher:
    """MQTT 발행 전용 워커 - 독립적인 이벤트 루프에서 실행"""
    
    def __init__(self, mqtt_client: 'MQTTClient', max_workers: int = 5):
        self.mqtt_client = mqtt_client
        self.max_workers = max_workers
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        
        # 발행 큐 (스레드 안전)
        self.publish_queue = Queue(maxsize=1000)
        
        # 발행 통계
        self.publish_stats = {
            'total_messages': 0,
            'successful_publishes': 0,
            'failed_publishes': 0,
            'queue_overflows': 0,
            'avg_publish_time': 0.0,
            'last_publish_time': None,
            # 추가: 페이로드 크기 집계(UTF-8 바이트 기준)
            'total_payload_bytes': 0,
            'last_payload_size': 0,
            'max_payload_size': 0
        }
        
        # 워커 상태
        self.workers_running = False
        self.worker_threads = []
        self.shutdown_event = threading.Event()
        
        # 성능 모니터링
        self.publish_times = []
        self.max_publish_time_samples = 100

        # 추가: 토픽별 페이로드 집계
        self.topic_stats = {}
        # 경량 동기화 (워커 수가 적어 dict 갱신 경쟁 가능성 낮음)
        # 필요시 threading.Lock() 도입 가능
        
        self.logger.info(f"🚀 MQTT 발행 워커 초기화 완료 - 최대 워커 수: {max_workers}")
    
    def start_workers(self):
        """발행 워커 스레드들을 시작합니다"""
        if self.workers_running:
            return
            
        self.workers_running = True
        self.shutdown_event.clear()
        
        # 여러 워커 스레드 생성
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._publisher_worker,
                name=f"MQTTPublisher-{i}",
                daemon=True
            )
            worker.start()
            self.worker_threads.append(worker)
            
        self.logger.info(f"✅ MQTT 발행 워커 {self.max_workers}개 시작됨")
    
    def stop_workers(self):
        """모든 워커 스레드를 종료합니다"""
        if not self.workers_running:
            return
            
        self.workers_running = False
        self.shutdown_event.set()
        
        # 워커 스레드들 종료 대기
        for worker in self.worker_threads:
            worker.join(timeout=5)
            
        self.worker_threads.clear()
        self.logger.info("🛑 모든 MQTT 발행 워커 종료됨")
    
    def _publisher_worker(self):
        """발행 워커 - 별도 스레드에서 실행"""
        worker_name = threading.current_thread().name
        self.logger.info(f"🔄 {worker_name} 워커 시작")
        
        while self.workers_running and not self.shutdown_event.is_set():
            try:
                # 큐에서 메시지 가져오기 (1초 타임아웃)
                try:
                    message = self.publish_queue.get(timeout=1.0)
                except Empty:
                    continue
                
                # 🔧 메시지 처리 로그 추가
                self.logger.info(f"📋 {worker_name} 메시지 처리 시작: {message.topic}")
                
                # 메시지 발행 실행
                start_time = time.time()
                success = self._publish_message(message)
                publish_time = time.time() - start_time
                
                # 🔧 발행 결과 로그 추가
                if success:
                    self.logger.info(f"✅ {worker_name} 메시지 발행 성공: {message.topic} ({publish_time:.3f}초)")
                else:
                    self.logger.warning(f"⚠️ {worker_name} 메시지 발행 실패: {message.topic} ({publish_time:.3f}초)")
                
                # 통계 업데이트
                self._update_publish_stats(success, publish_time)
                
                # 큐 태스크 완료 표시
                self.publish_queue.task_done()
                
            except Exception as e:
                self.logger.error(f"❌ {worker_name} 워커 오류: {e}")
                time.sleep(0.1)
                
        self.logger.info(f"🛑 {worker_name} 워커 종료")
    
    def _publish_message(self, message: MQTTMessage) -> bool:
        """실제 MQTT 메시지 발행"""
        try:
            # 메시지 age 확인 (너무 오래된 메시지는 버림)
            if message.timestamp is None:
                self.logger.warning(f"⚠️ 타임스탬프 없는 메시지 버림: {message.topic}")
                return False
                
            age = time.time() - message.timestamp
            if age > 30:  # 30초 이상 된 메시지는 버림
                self.logger.warning(f"⚠️ 오래된 메시지 버림: {message.topic} (age: {age:.1f}s)")
                return False
            
            # MQTT 클라이언트 연결 확인
            if not self.mqtt_client.connected:
                self.logger.debug(f"📋 MQTT 연결 끊어짐 - 메시지 버림: {message.topic}")
                return False
            
            # JSON 직렬화
            json_payload = json.dumps(message.payload, ensure_ascii=False, default=str)
            # UTF-8 기준 실제 전송 바이트(한글 3바이트/문자) 측정
            payload_size = len(json_payload.encode('utf-8'))
            
            # 실제 발행
            result = self.mqtt_client.client.publish(
                message.topic, 
                json_payload, 
                message.qos, 
                message.retain
            )
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                # 크기 로그는 정보 과다를 막기 위해 debug 레벨로 남김
                self.logger.debug(f"✅ 메시지 발행 성공: {message.topic} ({payload_size} bytes)")
                # 집계 업데이트
                self.publish_stats['total_payload_bytes'] += payload_size
                self.publish_stats['last_payload_size'] = payload_size
                if payload_size > self.publish_stats['max_payload_size']:
                    self.publish_stats['max_payload_size'] = payload_size
                # 토픽별 집계
                ts = self.topic_stats.get(message.topic)
                if ts is None:
                    ts = {'count': 0, 'bytes': 0, 'max': 0}
                    self.topic_stats[message.topic] = ts
                ts['count'] += 1
                ts['bytes'] += payload_size
                if payload_size > ts['max']:
                    ts['max'] = payload_size
                return True
            else:
                self.logger.warning(f"⚠️ 메시지 발행 실패: {message.topic}, 코드: {result.rc}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ 메시지 발행 중 오류: {message.topic} - {e}")
            return False
    
    def _update_publish_stats(self, success: bool, publish_time: float):
        """발행 통계 업데이트"""
        self.publish_stats['total_messages'] += 1
        self.publish_stats['last_publish_time'] = time.time()
        
        if success:
            self.publish_stats['successful_publishes'] += 1
        else:
            self.publish_stats['failed_publishes'] += 1
        
        # 평균 발행 시간 계산
        self.publish_times.append(publish_time)
        if len(self.publish_times) > self.max_publish_time_samples:
            self.publish_times.pop(0)
        
        self.publish_stats['avg_publish_time'] = sum(self.publish_times) / len(self.publish_times)
    
    def queue_message(self, topic: str, payload: Dict[str, Any], qos: int = 0, retain: bool = False) -> bool:
        """메시지를 발행 큐에 추가"""
        try:
            message = MQTTMessage(topic, payload, qos, retain)
            self.publish_queue.put_nowait(message)
            
            self.logger.debug(f"📋 메시지 큐에 추가: {topic} (큐 크기: {self.publish_queue.qsize()})")
            return True
            
        except Exception as e:
            self.publish_stats['queue_overflows'] += 1
            self.logger.warning(f"⚠️ 발행 큐 가득참 - 메시지 버림: {topic}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """발행 통계 반환"""
        stats = self.publish_stats.copy()
        stats['queue_size'] = self.publish_queue.qsize()
        stats['workers_running'] = self.workers_running
        stats['active_workers'] = len(self.worker_threads)
        # 토픽별 상위 10개 요약(바이트 기준)
        try:
            top = sorted(
                (
                    {
                        'topic': t,
                        'count': v['count'],
                        'total_bytes': v['bytes'],
                        'avg_bytes': int(v['bytes'] / v['count']) if v['count'] else 0,
                        'max_bytes': v['max']
                    }
                    for t, v in self.topic_stats.items()
                ),
                key=lambda x: x['total_bytes'],
                reverse=True
            )[:10]
            stats['top_topics'] = top
        except Exception:
            stats['top_topics'] = []
        return stats


class MQTTClient:
    """MQTT 클라이언트 클래스"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        MQTT 클라이언트 초기화
        
        Args:
            config: MQTT 설정 딕셔너리
        """
        self.config = config
        
        # 유니크한 Client ID 생성 (충돌 방지)
        base_client_id = config.get('client_id', 'pms_client')
        timestamp = int(time.time())
        unique_id = str(uuid.uuid4())[:8]
        self.unique_client_id = f"{base_client_id}_{timestamp}_{unique_id}"
        
        self.client = mqtt.Client(client_id=self.unique_client_id)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False
        self.reconnect_attempts = 0
        # 🔧 config에서 재연결 시도 횟수 가져오기
        self.max_reconnect_attempts = config.get('connection_retry_count', 15)
        
        # 🔧 기본 토픽 설정 추가
        self.base_topic = config.get('base_topic', 'pms')
        
        # 구독 중인 토픽 목록 추가
        self.subscribed_topics = set()
        
        # 🚀 MQTT 발행 워커 초기화
        max_publish_workers = config.get('max_publish_workers', 5)
        self.publisher = MQTTPublisher(self, max_publish_workers)
        
        # 🔧 재연결 상태 관리 (폴링 블로킹 방지)
        self.is_reconnecting = False
        self.reconnect_lock = None
        self.reconnect_task = None
        self.last_reconnect_attempt = None
        self.reconnect_cooldown = 5  # 재연결 시도 간격 (초)
        
        # 건강성 체크 스레드
        self.health_check_thread = None
        self.health_check_running = False
        self.health_check_interval = config.get('health_check_interval', 30)
        
        # 콜백 설정
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.on_unsubscribe = self._on_unsubscribe
        
        # 사용자 정의 메시지 콜백
        self.message_callback = None
        
        # 개선된 재연결 설정
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        
        # Keep-alive 시간 단축 (더 빠른 연결 상태 감지)
        self.keepalive_interval = config.get('keepalive', 30)
        
        # 인증 설정
        if config.get('username') and config.get('password'):
            self.client.username_pw_set(config['username'], config['password'])
            
        self.logger.info(f"🆔 MQTT 클라이언트 ID 생성: {self.unique_client_id}")
        self.logger.info(f"🏷️ 기본 토픽: {self.base_topic}")
        self.logger.info(f"🔄 최대 재연결 시도: {self.max_reconnect_attempts}회")
        self.logger.info(f"🚀 최대 발행 워커 수: {max_publish_workers}")

        # 🔧 LWT (Last Will and Testament) 설정 - 비정상 종료 시 offline 상태 발행
        status_topic = f"{self.base_topic}/status"
        offline_payload = json.dumps({
            "status": "offline",
            "timestamp": datetime.now().isoformat(),
            "reason": "unexpected_disconnect"
        })
        self.client.will_set(status_topic, offline_payload, qos=1, retain=True)
        self.logger.info(f"💀 LWT 설정 완료: {status_topic} -> offline")
    
    def _ensure_async_components(self):
        """비동기 컴포넌트들이 초기화되지 않은 경우 생성"""
        try:
            loop = asyncio.get_running_loop()
            if self.reconnect_lock is None:
                self.reconnect_lock = asyncio.Lock()
        except RuntimeError:
            # 이벤트 루프가 없는 경우 - 나중에 초기화됨
            pass
    
    def _on_connect(self, client, userdata, flags, rc):
        """연결 콜백"""
        if rc == 0:
            self.connected = True
            self.reconnect_attempts = 0  # 재연결 카운터 리셋
            self.is_reconnecting = False  # 🔧 재연결 상태 리셋
            self.logger.info(f"✅ MQTT 브로커에 연결됨: {self.config['broker']}:{self.config['port']}")
            self.logger.info(f"📋 클라이언트 ID: {self.unique_client_id}")
            
            # 🔧 연결 성공 시 online 상태 발행
            status_topic = f"{self.base_topic}/status"
            online_payload = {
                "status": "online",
                "timestamp": datetime.now().isoformat(),
                "client_id": self.unique_client_id
            }
            # 중요: retain=True로 설정하여 나중에 접속한 클라이언트도 상태 확인 가능
            self.publish(status_topic, online_payload, qos=1, retain=True)
            self.logger.info(f"✅ PMS 상태 발행: online")
            
            # 🚀 발행 워커 시작
            self.publisher.start_workers()
            
            # 🔧 발행 워커 상태 확인
            publisher_stats = self.publisher.get_stats()
            self.logger.info(f"📊 발행 워커 상태: {publisher_stats.get('active_workers', 0)}개 워커 실행중")
            self.logger.info(f"📊 발행 워커 운영 상태: {publisher_stats.get('workers_running', False)}")
            
            # 🔧 재연결 시 구독 목록 복원
            if self.subscribed_topics:
                self.logger.info(f"🔄 재연결 후 구독 복원 시작: {len(self.subscribed_topics)}개 토픽")
                restored_count = 0
                failed_topics = []
                
                for topic in self.subscribed_topics.copy():
                    try:
                        result, mid = self.client.subscribe(topic, 0)
                        if result == mqtt.MQTT_ERR_SUCCESS:
                            self.logger.info(f"✅ 구독 복원 성공: {topic}")
                            restored_count += 1
                        else:
                            self.logger.error(f"❌ 구독 복원 실패: {topic} (코드: {result})")
                            failed_topics.append(topic)
                    except Exception as e:
                        self.logger.error(f"❌ 구독 복원 중 오류: {topic} - {e}")
                        failed_topics.append(topic)
                
                # 실패한 토픽들은 목록에서 제거 (재시도 방지)
                for topic in failed_topics:
                    self.subscribed_topics.discard(topic)
                
                self.logger.info(f"✅ 구독 복원 완료: {restored_count}/{len(self.subscribed_topics) + len(failed_topics)}개 성공")
                if failed_topics:
                    self.logger.warning(f"⚠️ 구독 복원 실패한 토픽: {failed_topics}")
            else:
                self.logger.info("📋 복원할 구독 목록 없음")
            
            # 건강성 체크 스레드 시작
            self._start_health_check()
            
        else:
            self.connected = False
            error_messages = {
                1: "잘못된 프로토콜 버전",
                2: "잘못된 클라이언트 ID",
                3: "서버 사용 불가",
                4: "잘못된 사용자명 또는 비밀번호",
                5: "권한 없음"
            }
            error_msg = error_messages.get(rc, f"알 수 없는 오류 (코드: {rc})")
            self.logger.error(f"❌ MQTT 연결 실패: {error_msg}")
    
    def _on_disconnect(self, client, userdata, rc):
        """연결 해제 콜백"""
        self.connected = False
        if rc != 0:
            self.logger.warning(f"⚠️ MQTT 연결이 예기치 않게 끊어짐 (코드: {rc})")
            # 🔧 비동기 재연결 시작
            self._trigger_background_reconnect()
        else:
            self.logger.info("🔌 MQTT 연결이 정상적으로 종료됨")
    
    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """구독 완료 콜백"""
        self.logger.info(f"✅ 토픽 구독 완료 - MID: {mid}, QoS: {granted_qos}")
    
    def _on_unsubscribe(self, client, userdata, mid):
        """구독 해제 완료 콜백"""
        self.logger.info(f"🔄 토픽 구독 해제 완료 - MID: {mid}")
    
    def _start_health_check(self):
        """건강성 체크 스레드 시작"""
        if not self.health_check_running:
            self.health_check_running = True
            self.health_check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
            self.health_check_thread.start()
            self.logger.info("🩺 MQTT 건강성 체크 스레드 시작")
    
    def _health_check_loop(self):
        """건강성 체크 루프 (백그라운드에서 실행)"""
        while self.health_check_running:
            try:
                time.sleep(self.health_check_interval)  # 설정된 간격으로 체크
                
                if not self.connected and not self.is_reconnecting:
                    self.logger.warning("🩺 건강성 체크: 연결 끊어짐 감지")
                    self._trigger_background_reconnect()
                else:
                    # 주기적으로 상태 로깅
                    if self.reconnect_attempts == 0:  # 정상 상태일 때만 간단히 로깅
                        self.logger.debug("🩺 건강성 체크: 연결 정상")
                        
            except Exception as e:
                self.logger.error(f"❌ 건강성 체크 중 오류: {e}")
    
    def _trigger_background_reconnect(self):
        """🔧 백그라운드 재연결 트리거 (논블로킹)"""
        try:
            # 이벤트 루프에서 재연결 태스크 시작
            loop = asyncio.get_running_loop()
            if not self.is_reconnecting and (not self.reconnect_task or self.reconnect_task.done()):
                self.reconnect_task = loop.create_task(self._background_reconnect())
                self.logger.info("🔄 백그라운드 재연결 태스크 시작")
        except RuntimeError:
            # 이벤트 루프가 없는 경우 스레드에서 시작
            if not self.is_reconnecting:
                thread = threading.Thread(target=self._threaded_reconnect, daemon=True)
                thread.start()
                self.logger.info("🔄 스레드 기반 재연결 시작")
    
    def _threaded_reconnect(self):
        """스레드 기반 재연결 (이벤트 루프가 없는 경우)"""
        import time
        
        if self.is_reconnecting:
            return
            
        self.is_reconnecting = True
        
        for attempt in range(1, self.max_reconnect_attempts + 1):
            if self.connected:
                break
                
            try:
                self.logger.info(f"🔄 재연결 시도 {attempt}/{self.max_reconnect_attempts}")
                
                # 기존 연결 정리
                try:
                    self.client.loop_stop()
                    time.sleep(1)
                except:
                    pass
                
                # 재연결 시도
                self.client.reconnect()
                self.client.loop_start()
                
                # 연결 완료 대기 (최대 10초)
                wait_time = 0
                while not self.connected and wait_time < 10:
                    time.sleep(1)
                    wait_time += 1
                
                if self.connected:
                    self.logger.info("✅ 재연결 성공")
                    self.is_reconnecting = False
                    return
                    
            except Exception as e:
                self.logger.error(f"❌ 재연결 시도 {attempt} 실패: {e}")
            
            if attempt < self.max_reconnect_attempts:
                wait_time = min(5 * attempt, 30)
                self.logger.info(f"⏰ {wait_time}초 후 재시도...")
                time.sleep(wait_time)
        
        self.logger.error("💥 모든 재연결 시도 실패")
        self.is_reconnecting = False

    async def _background_reconnect(self):
        """🔧 비동기 백그라운드 재연결"""
        if self.is_reconnecting:
            return
            
        self._ensure_async_components()
        
        # 타입 가드: reconnect_lock이 None이 아닌지 확인
        if self.reconnect_lock is None:
            self.logger.error("❌ reconnect_lock이 초기화되지 않음")
            return
            
        async with self.reconnect_lock:
            if self.is_reconnecting or self.connected:
                return
                
            self.is_reconnecting = True
            self.logger.info("🔄 비동기 재연결 시작")
            
            try:
                for attempt in range(1, self.max_reconnect_attempts + 1):
                    if self.connected:
                        break
                        
                    try:
                        self.logger.info(f"🔄 재연결 시도 {attempt}/{self.max_reconnect_attempts}")
                        
                        # 기존 연결 정리 (비동기)
                        await asyncio.get_event_loop().run_in_executor(
                            None, lambda: (self.client.loop_stop(), time.sleep(1))
                        )
                        
                        # 재연결 시도 (비동기)
                        await asyncio.get_event_loop().run_in_executor(
                            None, self.client.reconnect
                        )
                        
                        await asyncio.get_event_loop().run_in_executor(
                            None, self.client.loop_start
                        )
                        
                        # 연결 완료 대기 (비동기, 최대 10초)
                        for _ in range(10):
                            if self.connected:
                                break
                            await asyncio.sleep(1)
                        
                        if self.connected:
                            self.logger.info("✅ 비동기 재연결 성공")
                            self.reconnect_attempts = 0
                            return
                            
                    except Exception as e:
                        self.logger.error(f"❌ 재연결 시도 {attempt} 실패: {e}")
                    
                    if attempt < self.max_reconnect_attempts:
                        wait_time = min(5 * attempt, 30)
                        self.logger.info(f"⏰ {wait_time}초 후 재시도...")
                        await asyncio.sleep(wait_time)
                
                self.logger.error("💥 모든 비동기 재연결 시도 실패")
                
            finally:
                self.is_reconnecting = False
    
    def _on_publish(self, client, userdata, mid):
        """발행 콜백"""
        self.logger.debug(f"📤 메시지 발행 완료, MID: {mid}")
    
    def _on_message(self, client, userdata, msg):
        """메시지 수신 콜백"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            self.logger.info(f"📨 [MQTT 메시지 수신] 토픽: {topic}")
            
            if self.message_callback:
                # JSON 파싱 시도
                try:
                    json_payload = json.loads(payload)
                    self.logger.debug(f"📄 [수신 내용] {json_payload}")
                except json.JSONDecodeError:
                    json_payload = {"raw_message": payload}
                    self.logger.warning(f"⚠️ JSON 파싱 실패, 원본 텍스트로 처리: {payload}")
                
                # 🔧 코루틴 콜백 안전 처리
                def run_callback_safe():
                    try:
                        self.logger.info(f"🧵 콜백 실행 시작: {threading.current_thread().name}")
                        
                        # 타입 가드: callback이 None이 아닌지 재확인
                        if self.message_callback is not None:
                            # 🔧 콜백이 코루틴인지 확인
                            if inspect.iscoroutinefunction(self.message_callback):
                                self.logger.info("🔄 코루틴 콜백 감지 - 비동기 실행")
                                
                                # 새로운 이벤트 루프에서 코루틴 실행
                                try:
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                    loop.run_until_complete(self.message_callback(topic, json_payload))
                                    loop.close()
                                    self.logger.info("✅ 코루틴 콜백 실행 완료")
                                except Exception as coro_error:
                                    self.logger.error(f"❌ 코루틴 콜백 실행 중 오류: {coro_error}")
                                    import traceback
                                    self.logger.error(f"❌ 코루틴 스택 트레이스:\n{traceback.format_exc()}")
                                    
                            else:
                                # 일반 함수인 경우 직접 실행
                                self.logger.info("🔄 일반 함수 콜백 실행")
                                self.message_callback(topic, json_payload)
                                self.logger.info("✅ 일반 콜백 실행 완료")
                        else:
                            self.logger.warning(f"⚠️ 콜백이 None으로 변경됨")
                            
                    except Exception as callback_error:
                        self.logger.error(f"❌ 콜백 실행 중 오류: {callback_error}")
                        import traceback
                        self.logger.error(f"❌ 콜백 스택 트레이스:\n{traceback.format_exc()}")
                
                # 별도 스레드에서 안전하게 실행
                thread = threading.Thread(target=run_callback_safe, daemon=True)
                thread.start()
                self.logger.info(f"🧵 콜백 스레드 시작: {thread.name}")
            else:
                self.logger.warning(f"⚠️ 메시지 콜백이 설정되지 않음 - 토픽: {topic}")
                self.logger.warning(f"⚠️ message_callback 상태: {self.message_callback}")
        except Exception as e:
            self.logger.error(f"❌ 메시지 처리 중 오류: {e}")
            import traceback
            self.logger.error(f"❌ 스택 트레이스:\n{traceback.format_exc()}")
    
    async def connect(self):
        """MQTT 브로커에 연결"""
        try:
            self.logger.info(f"🔌 MQTT 브로커 연결 시도: {self.config['broker']}:{self.config['port']}")
            
            self._ensure_async_components()
            
            # 비동기 연결을 위한 루프
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                self.client.connect, 
                self.config['broker'], 
                self.config['port'], 
                self.keepalive_interval
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
        # 🔧 재연결 태스크 정지
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
        
        # 🚀 발행 워커 정지
        self.publisher.stop_workers()
        
        # 건강성 체크 스레드 정지
        self.health_check_running = False
        if self.health_check_thread and self.health_check_thread.is_alive():
            self.health_check_thread.join(timeout=5)
            self.logger.info("🩺 건강성 체크 스레드 정지됨")
        
        if self.client:
            # 🔧 정상 종료 시 offline 상태 발행
            try:
                status_topic = f"{self.base_topic}/status"
                offline_payload = {
                    "status": "offline",
                    "timestamp": datetime.now().isoformat(),
                    "reason": "graceful_shutdown"
                }
                # 동기식 publish 사용 (워커가 이미 종료되었을 수 있음)
                self.client.publish(status_topic, json.dumps(offline_payload), qos=1, retain=True)
                self.logger.info(f"✅ PMS 상태 발행: offline (정상 종료)")
            except Exception as e:
                self.logger.error(f"❌ 종료 상태 발행 실패: {e}")

            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False
            # 🔧 정상 종료 시에만 구독 목록 clear (재연결 시 복원 방지)
            # subscribed_topics는 예기치 않은 연결 끊어짐에서 복원용으로 유지
            self.logger.info("🔌 MQTT 연결 해제됨 (구독 목록 유지)")
    
    def shutdown(self):
        """🔧 완전 종료 시 구독 목록 정리"""
        if self.subscribed_topics:
            self.logger.info(f"🧹 완전 종료: 구독 목록 정리 ({len(self.subscribed_topics)}개 토픽)")
            self.subscribed_topics.clear()
        
        # 🚀 발행 워커 완전 종료
        self.publisher.stop_workers()
    
    def publish(self, topic: str, payload: Dict[str, Any], qos: int = 0, retain: bool = False, retry_count: Optional[int] = None):
        """
        🚀 개선된 논블로킹 메시지 발행 - 독립적인 워커에서 처리
        
        Args:
            topic: MQTT 토픽
            payload: 발행할 데이터 (딕셔너리)
            qos: QoS 레벨 (0, 1, 2)
            retain: Retain 플래그
            retry_count: 재시도 횟수 (미사용 - 호환성 유지)
        """
        # 🚀 발행 워커를 통한 비동기 발행
        success = self.publisher.queue_message(topic, payload, qos, retain)
        
        if success:
            self.logger.debug(f"📋 메시지 발행 큐에 추가: {topic}")
        else:
            self.logger.warning(f"⚠️ 메시지 발행 큐 추가 실패: {topic}")
        
        return success
    
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
    
    def generate_topic(self, *parts: str) -> str:
        """
        🔧 기본 토픽을 이용하여 토픽을 생성합니다
        
        Args:
            *parts: 토픽 세그먼트들
            
        Returns:
            완성된 토픽 문자열
            
        Example:
            generate_topic("control", "device1", "command") -> "pms/control/device1/command"
        """
        return f"{self.base_topic}/{'/'.join(parts)}"
    
    def get_base_topic(self) -> str:
        """기본 토픽 반환"""
        return self.base_topic
    
    def get_subscribed_topics(self) -> set:
        """현재 구독 중인 토픽 목록 반환"""
        return self.subscribed_topics.copy()
    
    def get_queue_status(self) -> Dict[str, Any]:
        """🚀 큐 상태 조회 - 발행 워커 통계 포함"""
        publisher_stats = self.publisher.get_stats()
        
        return {
            'is_reconnecting': self.is_reconnecting,
            'base_topic': self.base_topic,
            'max_reconnect_attempts': self.max_reconnect_attempts,
            'publisher_stats': publisher_stats
        }
    
    def log_status(self):
        """현재 MQTT 클라이언트 상태 로깅"""
        queue_status = self.get_queue_status()
        publisher_stats = queue_status['publisher_stats']
        
        self.logger.info(f"🔍 [MQTT 상태 점검]")
        self.logger.info(f"   📡 연결 상태: {'연결됨' if self.connected else '연결 안됨'}")
        self.logger.info(f"   🔄 재연결 중: {'예' if self.is_reconnecting else '아니오'}")
        self.logger.info(f"   🏠 브로커: {self.config['broker']}:{self.config['port']}")
        self.logger.info(f"   🏷️ 기본 토픽: {self.base_topic}")
        self.logger.info(f"   📋 클라이언트 ID: {self.unique_client_id}")
        self.logger.info(f"   🔄 최대 재연결 시도: {self.max_reconnect_attempts}회")
        self.logger.info(f"   📡 구독 토픽 수: {len(self.subscribed_topics)}")
        
        # 🚀 발행 워커 상태
        self.logger.info(f"   🚀 [발행 워커 상태]")
        self.logger.info(f"      활성 워커: {publisher_stats['active_workers']}개")
        self.logger.info(f"      대기 메시지: {publisher_stats['queue_size']}개")
        self.logger.info(f"      총 발행: {publisher_stats['total_messages']}개")
        self.logger.info(f"      성공: {publisher_stats['successful_publishes']}개")
        self.logger.info(f"      실패: {publisher_stats['failed_publishes']}개")
        self.logger.info(f"      평균 발행 시간: {publisher_stats['avg_publish_time']:.3f}초")
        # 추가: 페이로드 크기 집계
        total_payload_mb = publisher_stats.get('total_payload_bytes', 0) / (1024 * 1024)
        self.logger.info(f"      누적 페이로드 크기: {total_payload_mb:.2f} MB")
        self.logger.info(f"      최근 페이로드 크기: {publisher_stats.get('last_payload_size', 0)} bytes")
        self.logger.info(f"      최대 페이로드 크기: {publisher_stats.get('max_payload_size', 0)} bytes")
        # 토픽 상위 목록
        top_topics = publisher_stats.get('top_topics') or []
        if top_topics:
            self.logger.info("      상위 토픽(총 바이트 기준, 최대 10개):")
            for t in top_topics:
                self.logger.info(
                    f"         - {t['topic']}: {t['total_bytes']} bytes, {t['count']}건, avg {t['avg_bytes']} bytes, max {t['max_bytes']} bytes"
                )
        
        if self.subscribed_topics:
            for topic in sorted(self.subscribed_topics):
                self.logger.info(f"      - {topic}")
        
        self.logger.info(f"   🔄 메시지 콜백: {'설정됨' if self.message_callback else '설정 안됨'}") 