"""
PMS (Power Management System) 메인 애플리케이션
- 설정 파일을 읽어 장비 핸들러들을 생성
- 스케줄러를 통해 주기적으로 데이터 폴링 및 MQTT 발행
- 운전 모드 관리자를 통해 기본/자동 운전 모드 지원
"""

import asyncio
import yaml
import logging
from pathlib import Path
import json

from pms_app.core.scheduler import PMSScheduler
from pms_app.core.mqtt_client import MQTTClient
from pms_app.devices import DeviceFactory
from pms_app.automation import OperationManager
from pms_app.utils.logger import setup_logger


def load_config():
    """설정 파일을 로드합니다."""
    config_path = Path(__file__).parent / "config" / "config.yml"
    with open(config_path, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)


async def main():
    """메인 애플리케이션 실행"""
    # 로거 설정
    logger = setup_logger("PMS_Main")
    logger.info("PMS 애플리케이션 시작")
    
    # 변수 초기화
    mqtt_client = None
    scheduler = None
    operation_manager = None
    
    try:
        # 설정 로드
        config = load_config()
        logger.info("설정 파일 로드 완료")
        
        # MQTT 클라이언트 초기화
        logger.info("🔌 MQTT 클라이언트 초기화 중...")
        mqtt_client = MQTTClient(config['mqtt'])
        logger.info("🔗 MQTT 브로커 연결 시도...")
        await mqtt_client.connect()
        logger.info("✅ MQTT 클라이언트 연결 완료")
        
        # MQTT 클라이언트 상태 확인
        mqtt_client.log_status()
        
        # 장비 핸들러 생성
        logger.info("🔧 장비 핸들러 생성 중...")
        device_handlers = []
        device_handler_map = {}
        
        for device_config in config['devices']:
            handler = DeviceFactory.create_device(device_config, mqtt_client, config)
            if handler is not None:
            device_handlers.append(handler)
                device_handler_map[device_config['name']] = handler
                logger.info(f"✅ 장비 핸들러 생성 성공: {device_config['name']} ({device_config['type']})")
            else:
                logger.warning(f"⚠️ 장비 핸들러 생성 실패 (비활성화): {device_config['name']} ({device_config['type']})")
        
        # 운전 모드 관리자 초기화
        logger.info("🎛️ 운전 모드 관리자 초기화 중...")
        operation_manager = OperationManager(config, device_handler_map, mqtt_client)
        await operation_manager.initialize()
        logger.info("✅ 운전 모드 관리자 초기화 완료")
        
        # 스케줄러 초기화 및 작업 등록
        logger.info("⏰ 스케줄러 초기화 중...")
        scheduler = PMSScheduler()
        for handler in device_handlers:
            scheduler.add_polling_job(handler)
            logger.info(f"   📋 스케줄링 작업 등록: {handler.name}")
        
        # 스케줄러 시작
        logger.info("▶️ 스케줄러 시작...")
        scheduler.start()
        logger.info("✅ 스케줄러 시작 완료")
        
        # 시스템 준비 완료 로그
        logger.info("🎉 === PMS 시스템 준비 완료 ===")
        logger.info(f"📊 등록된 장비: {len(device_handlers)}개")
        logger.info(f"🤖 자동 운전 모드: {'활성화' if config.get('auto_mode', {}).get('enabled', False) else '비활성화'}")
        
        # MQTT 제어 토픽 정보 출력
        control_topics = operation_manager.get_control_topics()
        logger.info("📡 === MQTT 제어 토픽 ===")
        for topic_name, topic in control_topics.items():
            logger.info(f"   📌 {topic_name}: {topic}")
        
        logger.info("🔍 === 디버깅 정보 ===")
        logger.info(f"📡 MQTT 연결 상태: {'연결됨' if mqtt_client.is_connected() else '연결 안됨'}")
        logger.info(f"📡 구독 토픽 수: {len(mqtt_client.get_subscribed_topics())}")
        logger.info("⚠️ threshold_config 토픽이 수신되지 않으면 다음을 확인하세요:")
        logger.info("   1. MQTT 브로커 설정이 백엔드와 일치하는지")
        logger.info("   2. 토픽 이름이 정확한지 (pms/control/threshold_config)")
        logger.info("   3. 네트워크 연결 상태")
        logger.info("   4. 백엔드에서 실제로 메시지를 발행했는지")
        
        # 애플리케이션 실행 유지
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("애플리케이션 종료 신호 받음")
        
    except Exception as e:
        logger.error(f"애플리케이션 실행 중 오류 발생: {e}")
        raise
    finally:
        # 정리 작업
        logger.info("PMS 시스템 종료 중...")
        
        if operation_manager is not None:
            try:
                await operation_manager.shutdown()
                logger.info("운전 모드 관리자 종료 완료")
            except Exception as e:
                logger.error(f"운전 모드 관리자 종료 중 오류: {e}")
        
        if scheduler is not None:
            try:
            scheduler.shutdown()
                logger.info("스케줄러 종료 완료")
            except Exception as e:
                logger.error(f"스케줄러 종료 중 오류: {e}")
        
        if mqtt_client is not None:
            try:
            await mqtt_client.disconnect()
                logger.info("MQTT 클라이언트 연결 해제 완료")
            except Exception as e:
                logger.error(f"MQTT 클라이언트 연결 해제 중 오류: {e}")
        
        logger.info("PMS 애플리케이션 종료 완료")


if __name__ == "__main__":
    asyncio.run(main()) 