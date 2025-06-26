"""
데이터 매니저 모듈
백그라운드 서버와 GUI 간의 실시간 데이터 공유를 관리
"""

import threading
import time
from typing import Dict, Any, List, Optional
from datetime import datetime


class SharedDataManager:
    """백그라운드 서버와 GUI 간 데이터 공유 관리 클래스"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """싱글톤 패턴 구현"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """데이터 매니저 초기화"""
        if hasattr(self, 'initialized'):
            return
        
        self.initialized = True
        self.data_lock = threading.RLock()
        
        # 장비별 최신 데이터 저장
        self.device_data: Dict[str, Dict[str, Any]] = {}
        
        # 장비 상태 정보
        self.device_status: Dict[str, Dict[str, Any]] = {}
        
        # MQTT 클라이언트 참조
        self.mqtt_client = None
        
        # 장비 핸들러 참조
        self.device_handlers: List = []
        
        # 시스템 상태
        self.system_status = {
            'running': False,
            'mqtt_connected': False,
            'last_update': None
        }
    
    def set_mqtt_client(self, mqtt_client):
        """MQTT 클라이언트 설정"""
        with self.data_lock:
            self.mqtt_client = mqtt_client
    
    def set_device_handlers(self, handlers: List):
        """장비 핸들러 목록 설정"""
        with self.data_lock:
            self.device_handlers = handlers
            
            # 장비별 초기 상태 설정
            for handler in handlers:
                self.device_status[handler.name] = {
                    'name': handler.name,
                    'type': handler.device_type,
                    'ip': handler.ip,
                    'port': handler.port,
                    'connected': False,
                    'last_successful_read': None,
                    'poll_interval': handler.poll_interval
                }
    
    def update_device_data(self, device_name: str, data: Dict[str, Any]):
        """장비 데이터 업데이트"""
        with self.data_lock:
            self.device_data[device_name] = {
                'timestamp': datetime.now(),
                'data': data
            }
            self.system_status['last_update'] = datetime.now()
    
    def update_device_status(self, device_name: str, status: Dict[str, Any]):
        """장비 상태 업데이트"""
        with self.data_lock:
            if device_name in self.device_status:
                self.device_status[device_name].update(status)
                self.device_status[device_name]['last_status_update'] = datetime.now()
    
    def get_device_data(self, device_name: str) -> Optional[Dict[str, Any]]:
        """특정 장비의 최신 데이터 조회"""
        with self.data_lock:
            return self.device_data.get(device_name)
    
    def get_device_status(self, device_name: str) -> Optional[Dict[str, Any]]:
        """특정 장비의 상태 정보 조회"""
        with self.data_lock:
            return self.device_status.get(device_name)
    
    def get_all_device_data(self) -> Dict[str, Dict[str, Any]]:
        """모든 장비의 데이터 조회"""
        with self.data_lock:
            return self.device_data.copy()
    
    def get_all_device_status(self) -> Dict[str, Dict[str, Any]]:
        """모든 장비의 상태 조회"""
        with self.data_lock:
            return self.device_status.copy()
    
    def update_system_status(self, **kwargs):
        """시스템 상태 업데이트"""
        with self.data_lock:
            self.system_status.update(kwargs)
            self.system_status['last_update'] = datetime.now()
    
    def get_system_status(self) -> Dict[str, Any]:
        """시스템 상태 조회"""
        with self.data_lock:
            status = self.system_status.copy()
            
            # MQTT 연결 상태 실시간 확인
            if self.mqtt_client:
                status['mqtt_connected'] = self.mqtt_client.is_connected()
            
            return status
    
    def is_data_fresh(self, device_name: str, max_age_seconds: int = 300) -> bool:
        """데이터가 신선한지 확인 (기본 5분)"""
        with self.data_lock:
            device_data = self.device_data.get(device_name)
            if not device_data:
                return False
            
            timestamp = device_data['timestamp']
            age = (datetime.now() - timestamp).total_seconds()
            return age <= max_age_seconds
    
    def get_device_handler(self, device_name: str):
        """특정 장비의 핸들러 조회"""
        with self.data_lock:
            for handler in self.device_handlers:
                if handler.name == device_name:
                    return handler
            return None
    
    def cleanup(self):
        """리소스 정리"""
        with self.data_lock:
            self.device_data.clear()
            self.device_status.clear()
            self.device_handlers.clear()
            self.mqtt_client = None


# 전역 데이터 매니저 인스턴스
data_manager = SharedDataManager() 