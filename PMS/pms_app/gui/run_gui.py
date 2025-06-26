"""
PMS GUI 실행 스크립트
"""

import sys
import os
import yaml
from pathlib import Path

# 패키지 경로 설정
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import PMSMainWindow
from utils.logger import setup_logger


def load_config():
    """설정 파일을 로드합니다."""
    config_path = Path(__file__).parent.parent / "config" / "config.yml"
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"설정 파일을 찾을 수 없습니다: {config_path}")
        # 기본 설정 사용
        return {
            'mqtt': {
                'broker': 'localhost',
                'port': 1883,
                'client_id': 'pms_gui_client'
            },
            'devices': [
                {
                    'name': 'Rack1_BMS',
                    'type': 'BMS',
                    'ip': '192.168.1.10',
                    'poll_interval': 2
                },
                {
                    'name': 'Farm_DCDC',
                    'type': 'DCDC',
                    'ip': '192.168.1.20',
                    'poll_interval': 1
                },
                {
                    'name': 'Unit1_PCS',
                    'type': 'PCS',
                    'ip': '192.168.1.30',
                    'poll_interval': 3
                }
            ]
        }
    except yaml.YAMLError as e:
        print(f"설정 파일 파싱 오류: {e}")
        sys.exit(1)


def main():
    """GUI 메인 함수"""
    print("PMS GUI 모드 시작...")
    
    try:
        # 로거 설정
        logger = setup_logger("PMS_GUI")
        logger.info("PMS GUI 애플리케이션 시작")
        
        # 설정 로드
        config = load_config()
        logger.info("설정 파일 로드 완료")
        
        # GUI 애플리케이션 생성 및 실행
        app = PMSMainWindow(config)
        app.run()
        
    except Exception as e:
        print(f"GUI 실행 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main() 