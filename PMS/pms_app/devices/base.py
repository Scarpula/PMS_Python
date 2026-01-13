"""
기본 장비 인터페이스
모든 장비 핸들러가 상속받아야 하는 추상 클래스
"""

import json
import logging
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.mqtt_client import MQTTClient


class DeviceInterface(ABC):
    """장비 핸들러의 기본 인터페이스"""
    
    def __init__(self, device_config: Dict[str, Any], mqtt_client: 'MQTTClient', system_config: Dict[str, Any]):
        """
        기본 초기화
        
        Args:
            device_config: 장비 설정 딕셔너리
            mqtt_client: MQTT 클라이언트 인스턴스
            system_config: 시스템 설정 딕셔너리 (simulation_mode, connection_timeout 등 포함)
        """
        self.name = device_config['name']
        self.device_type = device_config['type']
        self.ip = device_config['ip']
        self.port = device_config.get('port', 502)
        self.slave_id = device_config.get('slave_id', 1)
        self.poll_interval = device_config.get('poll_interval', 5)
        
        self.connection_timeout = system_config.get('connection_timeout', 2) # 기본값 2초 (빠른 실패)
        
        self.mqtt_client = mqtt_client
        self.logger = logging.getLogger(f"{self.__class__.__name__}_{self.name}")
        
        # 장비 맵 로드
        self.device_map = self._load_device_map()
        
        # 연결 상태
        self.connected = False
        self.last_successful_read = None
        
        # asyncio Lock은 사용 시점에 생성 (이벤트 루프 충돌 방지)
        self._connection_lock: Optional[asyncio.Lock] = None
        
        self.logger.info(f"장비 핸들러 초기화 완료: {self.name} ({self.device_type})")
    
    def _get_connection_lock(self) -> asyncio.Lock:
        """
        현재 이벤트 루프에서 완전히 독립적인 connection lock을 생성
        이벤트 루프 충돌 문제를 방지하기 위해 절대로 Lock을 저장하지 않음
        """
        import time
        import threading
        
        try:
            # 현재 스레드와 이벤트 루프 정보
            current_thread = threading.current_thread().name
            current_loop = asyncio.get_running_loop()
            loop_id = id(current_loop)
            timestamp = int(time.time() * 1000000)  # 마이크로초 타임스탬프
            
            # 완전히 새로운 Lock 생성 (절대로 저장하지 않음)
            lock = asyncio.Lock()
            lock_id = id(lock)
            
            self.logger.debug(f"🔒 {self.device_type} 새 Lock 생성: ID={lock_id}, 스레드={current_thread}, 루프={loop_id}, 시간={timestamp}")
            return lock
            
        except RuntimeError as e:
            # 이벤트 루프가 없는 경우
            self.logger.error(f"❌ {self.device_type} 이벤트 루프 없음: {e}")
            # 새 이벤트 루프 생성 시도
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                lock = asyncio.Lock()
                self.logger.warning(f"⚠️ {self.device_type} 새 이벤트 루프에서 Lock 생성: {id(lock)}")
                return lock
            except Exception as loop_error:
                self.logger.error(f"❌ {self.device_type} 새 루프 생성 실패: {loop_error}")
                # 마지막 수단: 스레드로컬 Lock
                return self._create_thread_local_lock()
        except Exception as e:
            self.logger.error(f"❌ {self.device_type} Lock 생성 일반 오류: {e}")
            return self._create_thread_local_lock()
    
    def _create_thread_local_lock(self) -> asyncio.Lock:
        """스레드 로컬 Lock 생성 (최후의 수단)"""
        import threading
        if not hasattr(self, '_thread_local'):
            self._thread_local = threading.local()
        
        # 스레드별로 다른 Lock 생성
        if not hasattr(self._thread_local, 'lock'):
            self._thread_local.lock = asyncio.Lock()
            self.logger.warning(f"🧵 {self.device_type} 스레드 로컬 Lock 생성: {id(self._thread_local.lock)}")
        
        return self._thread_local.lock
    


    def _load_device_map(self) -> Dict[str, Any]:
        """
        장비별 Modbus 맵 파일을 로드합니다.
        
        Returns:
            로드된 장비 맵 딕셔너리
        """
        map_file = f"{self.device_type.lower()}_map.json"
        
        try:
            config_path = Path(__file__).parent.parent.parent / "config" / map_file
            
            with open(config_path, 'r', encoding='utf-8') as file:
                device_map = json.load(file)
            
            self.logger.info(f"장비 맵 로드 완료: {map_file}")
            return device_map
            
        except FileNotFoundError:
            self.logger.error(f"장비 맵 파일을 찾을 수 없음: {map_file}")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"장비 맵 파일 파싱 오류: {e}")
            raise
    
    @abstractmethod
    async def read_data(self) -> Optional[Dict[str, Any]]:
        """
        장비에서 데이터를 읽어옵니다.
        각 장비 타입에 맞게 구현되어야 합니다.
        
        Returns:
            읽어온 원시 데이터 딕셔너리 또는 None (실패 시)
        """
        pass
    
    @abstractmethod
    async def process_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        읽어온 원시 데이터를 가공합니다.
        각 장비 타입에 맞게 구현되어야 합니다.
        
        Args:
            raw_data: 원시 데이터 딕셔너리
            
        Returns:
            가공된 데이터 딕셔너리
        """
        pass
    
    async def poll_and_publish(self):
        """
        데이터를 읽고, 가공하고, MQTT로 발행하는 공통 로직
        - 폴링과 발행을 분리하여 독립적으로 처리
        """
        try:
            # 1. 데이터 폴링 (읽기 + 가공)
            processed_data = await self.poll_data()
            
            if processed_data is None:
                self.logger.warning(f"데이터 폴링 실패: {self.name}")
                return
            
            # 2. 비동기 발행 (폴링과 독립적으로 처리)
            await self.publish_data(processed_data)
            
        except Exception as e:
            self.logger.error(f"폴링 및 발행 중 오류 발생 - {self.name}: {e}")
            # 오류 상태를 데이터 매니저에 업데이트
            try:
                from ..core.data_manager import data_manager
                data_manager.update_device_status(self.name, {
                    'connected': False,
                    'last_error': str(e)
                })
            except:
                pass
    
    async def poll_data(self) -> Optional[Dict[str, Any]]:
        """
        데이터 폴링 (읽기 + 가공)
        
        Returns:
            가공된 데이터 (메타데이터 포함) 또는 None
        """
        try:
            # 데이터 매니저 import (순환 import 방지)
            from ..core.data_manager import data_manager
            
            # 1. 데이터 읽기
            raw_data = await self.read_data()
            
            if raw_data is None:
                self.logger.warning(f"데이터 읽기 실패: {self.name}")
                # 연결 실패 상태를 데이터 매니저에 업데이트
                data_manager.update_device_status(self.name, {
                    'connected': False,
                    'last_error': '데이터 읽기 실패'
                })
                return None
            
            # 2. 데이터 가공
            processed_data = await self.process_data(raw_data)
            
            # 3. 메타데이터 추가
            final_data = self._add_metadata(processed_data)
            
            # 4. 데이터 매니저에 데이터 업데이트 (폴링 성공)
            data_manager.update_device_data(self.name, final_data)
            data_manager.update_device_status(self.name, {
                'connected': self.connected,
                'last_successful_read': self.last_successful_read
            })
            
            return final_data
            
        except Exception as e:
            self.logger.error(f"데이터 폴링 중 오류 발생 - {self.name}: {e}")
            return None
    
    async def publish_data(self, data: Dict[str, Any]):
        """
        데이터를 MQTT로 발행 (폴링과 독립적으로 처리)
        
        Args:
            data: 발행할 데이터 (메타데이터 포함)
        """
        try:
            # MQTT 발행 (비동기 큐 기반)
            topic = self._generate_topic()
            
            # 🔧 발행 전 상태 확인
            if not self.mqtt_client.connected:
                self.logger.warning(f"⚠️ MQTT 연결 끊어짐 - 발행 실패: {self.name}")
                return
            
            # 🔧 발행 워커 상태 확인
            publisher_stats = self.mqtt_client.publisher.get_stats()
            if not publisher_stats.get('workers_running', False):
                self.logger.error(f"❌ MQTT 발행 워커 정지됨 - 발행 실패: {self.name}")
                return
            
            self.logger.info(f"📤 MQTT 발행 시도: {self.name} -> {topic}")
            self.logger.debug(f"   📊 발행 워커 상태: {publisher_stats.get('active_workers', 0)}개 워커, 큐 크기: {publisher_stats.get('queue_size', 0)}")
            
            success = self.mqtt_client.publish(topic, data)
            
            if success:
                self.last_successful_read = datetime.now()
                self.logger.info(f"✅ 데이터 발행 큐 추가 성공: {self.name}")
            else:
                # 발행 실패는 경고 로그만 출력 (폴링에 영향 없음)
                self.logger.warning(f"⚠️ MQTT 발행 큐 추가 실패: {self.name} (폴링은 계속 진행)")
                
        except Exception as e:
            # 발행 오류는 폴링에 영향을 주지 않음
            self.logger.error(f"❌ MQTT 발행 중 오류 발생 - {self.name}: {e} (폴링은 계속 진행)")
    
    def _add_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        데이터에 메타정보를 추가합니다.
        
        Args:
            data: 가공된 데이터
            
        Returns:
            메타데이터가 추가된 데이터
        """
        return {
            "device_name": self.name,
            "device_type": self.device_type,
            "timestamp": datetime.now().isoformat(),
            "ip_address": self.ip,
            "data": data
        }
    
    def _generate_topic(self) -> str:
        """
        MQTT 토픽을 생성합니다.
        
        Returns:
            생성된 토픽 문자열
        """
        return f"pms/{self.device_type}/{self.name}/data"
    
    def get_status(self) -> Dict[str, Any]:
        """
        장비 핸들러의 현재 상태를 반환합니다.
        
        Returns:
            상태 정보 딕셔너리
        """
        return {
            "name": self.name,
            "type": self.device_type,
            "ip": self.ip,
            "port": self.port,
            "connected": self.connected,
            "last_successful_read": self.last_successful_read.isoformat() if self.last_successful_read else None,
            "poll_interval": self.poll_interval
        }

    def get_control_topic(self) -> str:
        """
        이 장비의 MQTT 제어 토픽을 반환합니다.
        예) pms/{device_type}/{device_name}/control
        """
        return f"pms/{self.device_type}/{self.name}/control"

    async def handle_control_message(self, payload: Dict[str, Any]):
        """
        MQTT 제어 토픽에서 수신된 메시지를 처리합니다.
        하위 클래스에서 필요한 경우 오버라이드하여 사용합니다.

        Args:
            payload: MQTT 메시지(JSON 파싱 결과)
        """
        self.logger.info(f"지원하지 않는 제어 메시지 수신: {payload}") 