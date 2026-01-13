"""
PMS (Power Management System) 메인 애플리케이션
- 설정 파일을 읽어 장비 핸들러들을 생성
- 스케줄러를 통해 주기적으로 데이터 폴링 및 MQTT 발행
- 운전 모드 관리자를 통해 수동/자동 운전 모드 지원
"""

import asyncio
import yaml
import logging
from pathlib import Path
import json
from datetime import datetime
from typing import Dict, Any, Optional

from pms_app.core.scheduler import PMSScheduler
from pms_app.core.mqtt_client import MQTTClient
from pms_app.core.db_config_loader import DBConfigLoader
from pms_app.devices import DeviceFactory
from pms_app.automation import OperationManager
from pms_app.utils.logger import setup_logger


def load_config() -> Dict[str, Any]:
    """설정 파일 로드"""
    config_path = Path(__file__).parent / "config" / "config.yml"
    
    with open(config_path, 'r', encoding='utf-8') as file:
        loaded_config = yaml.safe_load(file)
    
    if loaded_config is None:
        raise ValueError("설정 파일이 비어있습니다")
    
    return loaded_config


async def handle_control_command(device_handler_map: Dict[str, Any], mqtt_client: MQTTClient, topic: str, payload: str):
    """GUI에서 보낸 제어 명령 처리"""
    logger = logging.getLogger("PMS_Control")
    
    try:
        logger.info(f"🎯 제어 명령 처리 시작: {topic}")
        logger.info(f"📦 명령 페이로드: {payload}")
        
        # 토픽에서 장비 이름 추출: pms/control/{device_name}/command
        topic_parts = topic.split('/')
        if len(topic_parts) < 4 or topic_parts[0] != 'pms' or topic_parts[1] != 'control':
            logger.warning(f"❓ 잘못된 제어 토픽 형식: {topic}")
            return
        
        device_name = topic_parts[2]
        command_type = topic_parts[3]
        
        logger.info(f"🏷️ 장비명: {device_name}, 명령타입: {command_type}")
        
        if command_type != 'command':
            logger.warning(f"❓ 알 수 없는 명령 타입: {command_type}")
            return
        
        # JSON 파싱
        try:
            command_data = json.loads(payload)
            logger.info(f"✅ JSON 파싱 성공: {command_data}")
        except json.JSONDecodeError as e:
            logger.error(f"❌ 제어 명령 JSON 파싱 실패: {e}")
            logger.error(f"❌ 원본 페이로드: {payload}")
            return
        
        # 장비 핸들러 찾기
        logger.info(f"🔍 등록된 장비 목록: {list(device_handler_map.keys())}")
        
        if device_name not in device_handler_map:
            logger.error(f"❌ 알 수 없는 장비: {device_name}")
            await send_control_response(mqtt_client, device_name, command_data.get("gui_request_id"), 
                                      False, f"알 수 없는 장비: {device_name}")
            return
        
        device_handler = device_handler_map[device_name]
        logger.info(f"✅ 장비 핸들러 찾음: {device_name} ({type(device_handler).__name__})")
        
        # 명령 실행
        action = command_data.get('action')
        logger.info(f"🎬 액션 실행: {action}")
        
        if action == 'write_register':
            logger.info(f"📝 레지스터 쓰기 명령 실행 중...")
            success = await execute_write_register(device_handler, command_data, logger)
            
            logger.info(f"📊 명령 실행 결과: {'성공' if success else '실패'}")
            
            # 응답 전송
            response_msg = f"{'성공' if success else '실패'}: {command_data.get('description', '알 수 없는 명령')}"
            logger.info(f"📤 GUI 응답 준비: {response_msg}")
            
            await send_control_response(
                mqtt_client, 
                device_name, 
                command_data.get("gui_request_id"),
                success,
                response_msg
            )
        else:
            logger.warning(f"❓ 알 수 없는 액션: {action}")
            await send_control_response(mqtt_client, device_name, command_data.get("gui_request_id"), 
                                      False, f"지원하지 않는 액션: {action}")
    
    except Exception as e:
        logger.error(f"❌ 제어 명령 처리 중 오류: {e}")
        logger.error(f"❌ 오류 세부사항: topic={topic}, payload={payload}")
        import traceback
        logger.error(f"❌ 스택 트레이스:\n{traceback.format_exc()}")


async def execute_write_register(device_handler, command_data: Dict[str, Any], logger) -> bool:
    """레지스터 쓰기 명령 실행"""
    try:
        address = command_data.get('address')
        value = command_data.get('value')
        description = command_data.get('description', '레지스터 쓰기')
        
        logger.info(f"🔢 파라미터 추출: address={address}, value={value}, description={description}")
        
        # 타입 체크
        if address is None or value is None:
            logger.error(f"❌ 필수 파라미터 누락: address={address}, value={value}")
            return False
        
        if not isinstance(address, int):
            try:
                address = int(address)
                logger.info(f"🔄 주소 타입 변환: {address} (int)")
            except (ValueError, TypeError):
                logger.error(f"❌ 잘못된 주소 형식: {address}")
                return False
        
        logger.info(f"🔧 제어 명령 실행: {device_handler.name} - {description} (주소: {address}, 값: {value}, HEX: 0x{value:04X})")
        
        # 주소를 통해 레지스터 이름 찾기
        logger.info(f"🔍 레지스터 이름 검색 시작: 주소 {address}")
        register_name = find_register_name_by_address(device_handler, address)
        
        if register_name:
            logger.info(f"✅ 레지스터 이름 찾음: {register_name}")
            
            # 실제 레지스터 쓰기 수행
            logger.info(f"📝 Modbus write_register 호출: {register_name} = {value}")
            result = await device_handler.write_register(register_name, value)
            
            if result:
                logger.info(f"✅ 제어 명령 성공: {device_handler.name} - {description}")
                return True
            else:
                logger.error(f"❌ 제어 명령 실패: {device_handler.name} - {description}")
                logger.error(f"❌ write_register 반환값: {result}")
                return False
        else:
            logger.error(f"❌ 레지스터 이름을 찾을 수 없음: 주소 {address}")
            logger.error(f"❌ 사용 가능한 레지스터들을 확인하세요")
            return False
    
    except Exception as e:
        logger.error(f"❌ 레지스터 쓰기 실행 중 오류: {e}")
        logger.error(f"❌ 오류 파라미터: {command_data}")
        import traceback
        logger.error(f"❌ 스택 트레이스:\n{traceback.format_exc()}")
        return False


def find_register_name_by_address(device_handler, address: int) -> Optional[str]:
    """주소로부터 레지스터 이름 찾기"""
    logger = logging.getLogger("PMS_Control")
    
    try:
        # 장비 타입별로 메모리 맵에서 레지스터 이름 검색
        memory_map = device_handler.device_map
        logger.info(f"📋 메모리 맵 섹션: {list(memory_map.keys())}")
        
        # 제어 레지스터에서 검색
        control_registers = memory_map.get('control_registers', {})
        logger.info(f"🎛️ 제어 레지스터 검색: {len(control_registers)}개 레지스터")
        
        for register_name, register_info in control_registers.items():
            reg_address = register_info.get('address')
            logger.debug(f"   📍 {register_name}: 주소 {reg_address}")
            if reg_address == address:
                logger.info(f"✅ 제어 레지스터에서 찾음: {register_name} (주소: {address})")
                return register_name
        
        # 다른 섹션에서도 검색 (파라미터 등)
        sections = ['parameter_registers', 'data_registers', 'metering_registers']
        for section in sections:
            section_data = memory_map.get(section, {})
            logger.info(f"📂 {section} 검색: {len(section_data)}개 레지스터")
            
            for register_name, register_info in section_data.items():
                reg_address = register_info.get('address')
                if reg_address == address:
                    logger.info(f"✅ {section}에서 찾음: {register_name} (주소: {address})")
                    return register_name
        
        logger.warning(f"❌ 주소 {address}에 해당하는 레지스터를 찾을 수 없음")
        return None
    
    except Exception as e:
        logger.error(f"❌ 레지스터 이름 검색 오류: {e}")
        return None


async def send_control_response(mqtt_client: MQTTClient, device_name: str, request_id: str, success: bool, message: str):
    """제어 명령 응답 전송"""
    logger = logging.getLogger("PMS_Control")
    
    try:
        response_data = {
            "request_id": request_id,
            "success": success,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "device_name": device_name
        }
        
        response_topic = f"pms/control/{device_name}/response"
        
        logger.info(f"📤 응답 데이터 준비: {response_data}")
        logger.info(f"📡 응답 토픽: {response_topic}")
        
        # MQTT publish 메소드 호출 (동기 함수)
        mqtt_client.publish(response_topic, response_data)
        
        logger.info(f"✅ 제어 응답 전송 완료: {device_name} - {message}")
    
    except Exception as e:
        logger.error(f"❌ 제어 응답 전송 오류: {e}")
        logger.error(f"❌ 응답 데이터: {locals()}")


async def mqtt_connection_monitor(mqtt_client: MQTTClient, check_interval: int = 30):
    """MQTT 연결 상태 모니터링"""
    logger = logging.getLogger("MQTT_Monitor")
    logger.info(f"🔍 MQTT 연결 모니터링 시작 (체크 간격: {check_interval}초)")
    
    while True:
        try:
            await asyncio.sleep(check_interval)
            
            if not mqtt_client.is_connected():
                logger.warning("⚠️ MQTT 연결 끊어진 상태 감지 - 재연결 시도")
                current_time = datetime.now().strftime("%H:%M:%S")
                logger.info(f"[{current_time}] 🟡 MQTT 연결 끊어짐 - 재연결 시도 중...")
                
                # 수동으로 재연결 시도
                try:
                    await mqtt_client.connect()
                    if mqtt_client.is_connected():
                        logger.info("✅ MQTT 재연결 성공")
                    else:
                        logger.warning("⚠️ MQTT 재연결 실패 - 다음 체크에서 재시도")
                except Exception as e:
                    logger.error(f"❌ MQTT 재연결 시도 중 오류: {e}")
            else:
                logger.debug("💓 MQTT 연결 상태 양호")
                
        except asyncio.CancelledError:
            logger.info("🔌 MQTT 연결 모니터링 종료")
            break
        except Exception as e:
            logger.error(f"❌ MQTT 연결 모니터링 중 오류: {e}")


async def setup_control_message_handler(mqtt_client: MQTTClient, device_handler_map: Dict[str, Any], operation_manager=None):
    """제어 메시지 핸들러 설정"""
    logger = logging.getLogger("PMS_Control")
    
    async def control_message_callback(topic: str, payload: str):
        """통합 제어 메시지 콜백"""
        logger.info(f"📨 MQTT 메시지 수신: {topic}")
        logger.debug(f"📄 메시지 내용: {payload}")
        
        # 장비별 직접 제어 명령 처리 (pms/control/{device_name}/command)
        if '/control/' in topic and topic.endswith('/command'):
            logger.info(f"🎛️ 장비 제어 명령 감지: {topic}")
            await handle_control_command(device_handler_map, mqtt_client, topic, payload)
        
        # 운전 모드 관리자 메시지 처리
        elif operation_manager and '/control/' in topic:
            logger.info(f"🤖 운전 모드 제어 메시지 감지: {topic}")
            # operation_manager의 handle_mqtt_message_threadsafe를 스레드 안전하게 호출
            try:
                operation_manager.handle_mqtt_message_threadsafe(topic, payload)
            except Exception as e:
                logger.error(f"❌ 운전 모드 메시지 처리 중 오류: {e}")
        
        else:
            logger.debug(f"❓ 처리되지 않은 메시지: {topic}")
    
    # MQTT 클라이언트에 메시지 콜백 설정
    logger.info("🔧 통합 메시지 콜백 설정 중...")
    mqtt_client.set_message_callback(control_message_callback)
    logger.info("✅ 통합 메시지 콜백 설정 완료")
    
    # MQTT 연결 상태 재확인
    if not mqtt_client.is_connected():
        logger.error("❌ MQTT 클라이언트가 연결되지 않음 - 구독 실패 가능성")
        return
    
    logger.info("📡 제어 토픽 구독 시작...")
    
    # 모든 장비의 제어 토픽 구독
    for device_name in device_handler_map.keys():
        control_topic = f"pms/control/{device_name}/command"
        try:
            logger.info(f"📡 구독 시도: {control_topic}")
            await mqtt_client.subscribe(control_topic)
            logger.info(f"✅ 제어 토픽 구독 성공: {control_topic}")
        except Exception as e:
            logger.error(f"❌ 제어 토픽 구독 실패: {control_topic} - {e}")
    
    # 추가: 테스트 토픽도 구독해서 MQTT 메시지 수신이 작동하는지 확인
    test_topic = "pms/test/connection"
    try:
        logger.info(f"📡 테스트 토픽 구독 시도: {test_topic}")
        await mqtt_client.subscribe(test_topic)
        logger.info(f"✅ 테스트 토픽 구독 성공: {test_topic}")
    except Exception as e:
        logger.error(f"❌ 테스트 토픽 구독 실패: {test_topic} - {e}")
    
    logger.info("📡 모든 토픽 구독 완료")


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
        
        # DB에서 자동운전 모드 설정 로드 (활성화된 경우)
        if config.get('database', {}).get('enabled', False) and config.get('database', {}).get('load_config_from_db', False):
            try:
                logger.info("🗄️ DB에서 자동운전 모드 설정 로드 중...")
                
                # DB 설정 로더 초기화
                db_url = config['database']['url']
                device_location = config['database']['device_location']
                db_loader = DBConfigLoader(db_url, device_location)
                
                # DB 연결 테스트
                if await db_loader.test_connection():
                    # DB에서 자동운전 설정 로드
                    db_auto_config = await db_loader.load_auto_mode_config()
                    
                    # 기존 설정과 병합 (DB 값이 우선)
                    original_auto_config = config.get('auto_mode', {})
                    config['auto_mode'] = {**original_auto_config, **db_auto_config}
                    
                    logger.info("✅ DB 자동운전 설정 로드 및 병합 완료")
                    logger.info(f"🔋 최종 SOC 상한: {config['auto_mode']['soc_high_threshold']}%")
                    logger.info(f"🔋 최종 SOC 하한: {config['auto_mode']['soc_low_threshold']}%")
                    logger.info(f"🔋 최종 충전 정지: {config['auto_mode']['soc_charge_stop_threshold']}%")
                    logger.info(f"⏱️ 최종 DCDC 대기: {config['auto_mode']['dcdc_standby_time']}초")
                    logger.info(f"⚡ 최종 충전 전력: {config['auto_mode']['charging_power']}kW")
                else:
                    logger.warning("⚠️ DB 연결 실패 - 기본 설정 사용")
                    
            except Exception as e:
                logger.error(f"❌ DB 설정 로드 실패: {e}")
                logger.warning("⚠️ 기본 설정으로 계속 진행합니다")
        else:
            logger.info("ℹ️ DB 설정 로드가 비활성화됨 - 기본 설정 사용")
        
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
        
        # 운전 모드 관리자 초기화 (메시지 핸들러 설정 전에 생성)
        logger.info("🎛️ 운전 모드 관리자 초기화 중...")
        operation_manager = OperationManager(config, device_handler_map, mqtt_client, asyncio.get_event_loop())
        await operation_manager.initialize()
        logger.info("✅ 운전 모드 관리자 초기화 완료")
        
        # 통합 제어 명령 처리 시스템 설정 (운전 모드 관리자 포함)
        logger.info("🎛️ 통합 제어 명령 처리 시스템 설정 중...")
        await setup_control_message_handler(mqtt_client, device_handler_map, operation_manager)
        logger.info("✅ 통합 제어 명령 처리 시스템 설정 완료")
        
        # MQTT 구독 상태 확인
        logger.info("📡 === MQTT 구독 상태 확인 ===")
        subscribed_topics = mqtt_client.get_subscribed_topics()
        logger.info(f"📋 현재 구독 중인 토픽 수: {len(subscribed_topics)}")
        for topic in subscribed_topics:
            logger.info(f"   📌 구독 토픽: {topic}")
        
        # 메시지 콜백 상태 확인  
        callback_status = "설정됨" if mqtt_client.message_callback else "미설정"
        logger.info(f"🔄 메시지 콜백 상태: {callback_status}")
        
        # MQTT 클라이언트 ID 확인
        logger.info(f"🏷️ MQTT 클라이언트 ID: {mqtt_client.config.get('client_id', 'pms_client')}")
        
        # 테스트 메시지 발행 (선택사항)
        test_topic = "pms/test/connection"
        mqtt_client.publish(test_topic, {"test": "connection_check", "timestamp": "now"})
        logger.info(f"📤 연결 테스트 메시지 발행: {test_topic}")
        
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
        
        # MQTT 연결 모니터링 태스크 시작
        logger.info("🔍 MQTT 연결 모니터링 시작...")
        monitor_task = asyncio.create_task(mqtt_connection_monitor(mqtt_client, check_interval=30))
        
        # 애플리케이션 실행 유지
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("애플리케이션 종료 신호 받음")
            # MQTT 모니터링 태스크 취소
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        
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