"""
PMS GUI 모드 실행 스크립트
GUI 인터페이스로 PMS 시스템을 실행합니다.
"""

import yaml
import sys
from pathlib import Path

from pms_app.gui import PMSMainWindow
from pms_app.utils.logger import setup_logger


def load_config():
    """설정 파일을 로드합니다."""
    config_path = Path(__file__).parent / "config" / "config.yml"
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        print(f"설정 파일을 찾을 수 없습니다: {config_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"설정 파일 파싱 오류: {e}")
        sys.exit(1)


def main():
    """GUI 모드 메인 함수"""
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
        sys.exit(1)


if __name__ == "__main__":
    main() 