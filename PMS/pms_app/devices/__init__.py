"""
Devices 모듈
장비별 핸들러 클래스들과 DeviceFactory를 포함합니다.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from .base import DeviceInterface


class DeviceFactory:
    """장비 핸들러 생성을 위한 팩토리 클래스"""
    
    # 각 장비 타입별 필요한 파일들 정의
    _required_files = {
        'BMS': {
            'handler': 'bms_handler.py',
            'map': 'bms_map.json'
        },
        'DCDC': {
            'handler': 'dcdc_handler.py', 
            'map': 'dcdc_map.json'
        },
        'PCS': {
            'handler': 'pcs_handler.py',
            'map': 'pcs_map.json'
        }
    }
    
    @classmethod
    def _check_required_files(cls, device_type: str) -> bool:
        """
        장비 타입에 필요한 파일들이 존재하는지 확인합니다.
        
        Args:
            device_type: 장비 타입 ('BMS', 'DCDC', 'PCS')
            
        Returns:
            모든 필요한 파일이 존재하면 True, 아니면 False
        """
        import sys
        
        if device_type not in cls._required_files:
            return False
        
        required_files = cls._required_files[device_type]
        
        # PyInstaller로 실행되는 경우 (exe 환경)
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            # exe 환경에서는 config 디렉토리가 실행 파일과 같은 위치에 있음
            import sys
            exe_dir = Path(sys.executable).parent if hasattr(sys, 'executable') else Path.cwd()
            config_dir = exe_dir / "config"
            
            # 맵 파일만 확인 (핸들러는 exe에 포함되어 있음)
            map_file = config_dir / required_files['map']
            if not map_file.exists():
                print(f"정보: PyInstaller 환경에서 {device_type} 맵 파일이 없습니다: {map_file}")
                print(f"      {device_type} 장비가 비활성화됩니다.")
                return False
            
            print(f"정보: PyInstaller 환경에서 {device_type} 장비 활성화 - 맵 파일 확인됨")
            return True
        
        # 일반 Python 환경에서는 파일 체크
        current_dir = Path(__file__).parent
        config_dir = current_dir.parent.parent / "config"
        
        # 핸들러 파일 확인
        handler_file = current_dir / required_files['handler']
        if not handler_file.exists():
            print(f"경고: {device_type} 핸들러 파일이 없습니다: {handler_file}")
            return False
        
        # 맵 파일 확인
        map_file = config_dir / required_files['map']
        if not map_file.exists():
            print(f"경고: {device_type} 맵 파일이 없습니다: {map_file}")
            return False
        
        return True
    
    @classmethod
    def create_device(cls, device_config: Dict[str, Any], mqtt_client, system_config: Dict[str, Any]) -> Optional[DeviceInterface]:
        """
        장비 설정에 따라 적절한 핸들러 인스턴스를 생성합니다.
        
        Args:
            device_config: 장비 설정 딕셔너리 (name, type, ip, port 등 포함)
            mqtt_client: MQTT 클라이언트 인스턴스
            system_config: 시스템 설정 딕셔너리 (simulation_mode, connection_timeout 등 포함)
            
        Returns:
            생성된 장비 핸들러 인스턴스 또는 None (파일이 없는 경우)
            
        Raises:
            ValueError: 지원하지 않는 장비 타입인 경우
        """
        device_type = device_config.get('type')
        device_name = device_config.get('name', 'Unknown')
        
        # 장비 타입이 없으면 에러
        if not device_type:
            raise ValueError("장비 설정에 'type' 필드가 필요합니다.")
        
        # 지원되는 장비 타입인지 확인
        if device_type not in cls._required_files:
            supported_types = ', '.join(cls._required_files.keys())
            raise ValueError(f"지원하지 않는 장비 타입: {device_type}. 지원되는 타입: {supported_types}")
        
        # 필요한 파일들이 존재하는지 확인
        if not cls._check_required_files(device_type):
            print(f"정보: {device_name} ({device_type}) 장비는 필요한 파일이 없어 비활성화됩니다.")
            return None
        
        # 동적으로 핸들러 클래스 import 및 생성
        try:
            if device_type == 'BMS':
                from .bms_handler import BMSHandler
                return BMSHandler(device_config, mqtt_client, system_config)
            elif device_type == 'DCDC':
                from .dcdc_handler import DCDCHandler
                return DCDCHandler(device_config, mqtt_client, system_config)
            elif device_type == 'PCS':
                from .pcs_handler import PCSHandler
                return PCSHandler(device_config, mqtt_client, system_config)
        except ImportError as e:
            print(f"경고: {device_type} 핸들러를 import할 수 없습니다: {e}")
            return None
        except Exception as e:
            print(f"경고: {device_name} ({device_type}) 핸들러 생성 중 오류 발생: {e}")
            return None
    
    @classmethod
    def get_supported_types(cls) -> list:
        """지원되는 장비 타입 목록을 반환합니다."""
        return list(cls._required_files.keys())
    
    @classmethod
    def get_available_types(cls) -> list:
        """현재 사용 가능한 장비 타입 목록을 반환합니다."""
        available_types = []
        for device_type in cls._required_files.keys():
            if cls._check_required_files(device_type):
                available_types.append(device_type)
        return available_types


# 동적 import를 위해 __all__을 수정
__all__ = ['DeviceInterface', 'DeviceFactory'] 