"""
기본 장비 인터페이스
모든 장비 핸들러가 상속받아야 하는 추상 클래스
"""

import json
import logging
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
        
        self.connection_timeout = system_config.get('connection_timeout', 3) # 기본값 3초
        
        self.mqtt_client = mqtt_client
        self.logger = logging.getLogger(f"{self.__class__.__name__}_{self.name}")
        
        # 장비 맵 로드
        self.device_map = self._load_device_map()
        
        # 연결 상태
        self.connected = False
        self.last_successful_read = None
        
        self.logger.info(f"장비 핸들러 초기화 완료: {self.name} ({self.device_type})")
    
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
                return
            
            # 2. 데이터 가공
            processed_data = await self.process_data(raw_data)
            
            # 3. 메타데이터 추가
            final_data = self._add_metadata(processed_data)
            
            # 4. 데이터 매니저에 데이터 업데이트
            data_manager.update_device_data(self.name, final_data)
            data_manager.update_device_status(self.name, {
                'connected': self.connected,
                'last_successful_read': self.last_successful_read
            })
            
            # 5. MQTT 발행
            topic = self._generate_topic()
            success = self.mqtt_client.publish(topic, final_data)
            
            if success:
                self.last_successful_read = datetime.now()
                self.logger.debug(f"데이터 발행 성공: {self.name}")
            else:
                self.logger.warning(f"MQTT 발행 실패: {self.name}")
                
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