"""
PMS GUI + 서버 통합 실행 스크립트
GUI 인터페이스와 백그라운드 PMS 서버를 동시에 실행합니다.
"""

import asyncio
import threading
import yaml
import sys
import time
from pathlib import Path
from typing import Optional

from pms_app.gui.main_window import PMSMainWindow
from pms_app.core.scheduler import PMSScheduler
from pms_app.core.mqtt_client import MQTTClient
from pms_app.core.data_manager import data_manager
from pms_app.core.system_monitor import SystemMonitor
from pms_app.devices import DeviceFactory
from pms_app.automation import OperationManager
from pms_app.utils.logger import setup_logger
import json
from datetime import datetime
from typing import Optional, Dict, Any


class IntegratedPMSApp:
    """GUI + 서버 통합 PMS 애플리케이션"""
    
    def __init__(self):
        self.config = None
        self.logger = None
        self.mqtt_client = None
        self.scheduler = None
        self.device_handlers = []
        self.device_handler_map = {}
        self.operation_manager = None
        self.server_running = False
        self.server_thread = None
        self.gui_app = None
        self.system_monitor = None
    
    def load_config(self):
        """설정 파일을 로드합니다."""
        config_path = Path(__file__).parent / "config" / "config.yml"
        try:
            with open(config_path, 'r', encoding='utf-8') as file:
                self.config = yaml.safe_load(file)
                print(f"✅ 설정 파일 로드 완료: {config_path}")
                return True
        except FileNotFoundError:
            print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
            # 기본 설정 사용
            self.config = {
                'mqtt': {
                    'broker': 'localhost',
                    'port': 1883,
                    'client_id': 'pms_integrated_client'
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
            print("🔧 기본 설정을 사용합니다.")
            return True
        except yaml.YAMLError as e:
            print(f"❌ 설정 파일 파싱 오류: {e}")
            return False
    
    async def start_pms_server(self):
        """백그라운드 PMS 서버 시작"""
        print("\n🚀 PMS 서버 시작 중...")
        
        try:
            # 로거 설정
            self.logger = setup_logger("PMS_Integrated")
            self.logger.info("통합 PMS 애플리케이션 시작")
            
            # MQTT 클라이언트 초기화
            if self.config:
                print("🔌 MQTT 클라이언트 초기화 중...")
                self.mqtt_client = MQTTClient(self.config['mqtt'])
                print("🔗 MQTT 브로커 연결 시도...")
                await self.mqtt_client.connect()
                print("✅ MQTT 클라이언트 연결 완료")
                
                # MQTT 클라이언트 상태 확인
                if hasattr(self.mqtt_client, 'log_status'):
                    self.mqtt_client.log_status()
                
                # 데이터 매니저에 MQTT 클라이언트 설정
                data_manager.set_mqtt_client(self.mqtt_client)
                
                # 시스템 설정 추출
                system_config = self.config.get('system', {})
                
                # 장비 핸들러 생성
                print("🔧 장비 핸들러 생성 중...")
                self.device_handlers = []
                self.device_handler_map = {}
                
                for device_config in self.config['devices']:
                    try:
                        # DeviceFactory에 system_config 전달
                        handler = DeviceFactory.create_device(device_config, self.mqtt_client, system_config)
                        if handler is not None:
                            self.device_handlers.append(handler)
                            self.device_handler_map[device_config['name']] = handler
                            print(f"✅ 장비 핸들러 생성 성공: {device_config['name']} ({device_config['type']})")
                        else:
                            print(f"⚠️ 장비 핸들러 생성 실패 (비활성화): {device_config['name']} ({device_config['type']})")
                    except Exception as e:
                        print(f"  ❌ {device_config['name']} 생성 실패: {e}")
                
                # 데이터 매니저에 장비 핸들러 설정
                data_manager.set_device_handlers(self.device_handlers)
                
                # 제어 명령 처리 시스템 설정 추가
                print("🎛️ 제어 명령 처리 시스템 설정 중...")
                await self.setup_control_message_handler()
                print("✅ 제어 명령 처리 시스템 설정 완료")
                
                # 운전 모드 관리자 초기화 (실시간 상태 전송을 위해 필수!)
                print("🎛️ 운전 모드 관리자 초기화 중...")
                
                # 현재 이벤트 루프를 OperationManager에 전달
                main_loop = asyncio.get_running_loop()
                self.operation_manager = OperationManager(
                    config=self.config,
                    device_handlers=self.device_handler_map,
                    mqtt_client=self.mqtt_client,
                    main_loop=main_loop  # 메인 루프 전달
                )
                await self.operation_manager.initialize()
                print("✅ 운전 모드 관리자 초기화 완료 - 실시간 상태 전송 활성화")
                
            else:
                raise ValueError("설정이 로드되지 않았습니다")
            
            # 스케줄러 초기화 및 작업 등록
            print("⏰ 스케줄러 초기화 중...")
            self.scheduler = PMSScheduler()
            for handler in self.device_handlers:
                self.scheduler.add_polling_job(handler)
                print(f"   📋 스케줄링 작업 등록: {handler.name}")
            
            # 스케줄러 시작
            print("▶️ 스케줄러 시작...")
            await self.scheduler.start()
            print("✅ 스케줄러 시작 완료")
            
            # 🔍 시스템 모니터링 초기화
            print("🔍 시스템 모니터링 초기화 중...")
            self.system_monitor = SystemMonitor(self.config)
            self.system_monitor.set_components(
                self.scheduler,
                self.mqtt_client,
                self.device_handlers,
                data_manager
            )
            
            # 복구 콜백 추가
            self.system_monitor.add_recovery_callback(self._scheduler_recovery)
            self.system_monitor.add_recovery_callback(self._mqtt_recovery)
            self.system_monitor.set_emergency_handler(self._emergency_shutdown)
            
            # 시스템 모니터링 시작
            await self.system_monitor.start()
            print("✅ 시스템 모니터링 시작 완료")
            
            self.server_running = True
            
            # 데이터 매니저 시스템 상태 업데이트
            data_manager.update_system_status(running=True)
            
            # 서버 상태 모니터링
            await self.monitor_server()
            
        except Exception as e:
            print(f"❌ PMS 서버 시작 실패: {e}")
            if self.logger:
                self.logger.error(f"서버 시작 실패: {e}")
            self.server_running = False
    
    async def monitor_server(self):
        """서버 상태 모니터링"""
        print("🔍 서버 상태 모니터링 시작")

        # main.py 스타일의 초기화 로그 출력
        print("🎉 === PMS 시스템 준비 완료 ===")
        print(f"📊 등록된 장비: {len(self.device_handlers)}개")
        auto_mode_enabled = self.config.get('auto_mode', {}).get('enabled', False) if self.config else False
        print(f"🤖 자동 운전 모드: {'활성화' if auto_mode_enabled else '비활성화'}")

        if self.operation_manager:
            # 제어 토픽 정보 출력
            control_topics = self.operation_manager.get_control_topics()
            print("📡 === MQTT 제어 토픽 ===")
            for topic_name, topic in control_topics.items():
                print(f"   📌 {topic_name}: {topic}")

        print("🔍 === 디버깅 정보 ===")
        print(f"📡 MQTT 연결 상태: {'연결됨' if self.mqtt_client and self.mqtt_client.is_connected() else '연결 안됨'}")
        if self.mqtt_client:
            print(f"📡 구독 토픽 수: {len(self.mqtt_client.get_subscribed_topics())}")

        print("⚠️ threshold_config 토픽이 수신되지 않으면 다음을 확인하세요:")
        print("   1. MQTT 브로커 설정이 백엔드와 일치하는지")
        print("   2. 토픽 이름이 정확한지 (pms/control/threshold_config)")
        print("   3. 네트워크 연결 상태")
        print("   4. 백엔드에서 실제로 메시지를 발행했는지")

        while self.server_running:
            try:
                # 주기적으로 서버 상태 출력
                await asyncio.sleep(30)  # 30초마다

                if self.mqtt_client and self.mqtt_client.is_connected():
                    status_msg = f"🟢 PMS 서버 정상 동작 중 (장비: {len(self.device_handlers)}개)"
                    print(f"[{time.strftime('%H:%M:%S')}] {status_msg}")
                else:
                    status_msg = "🟡 MQTT 연결 끊어짐 - 재연결 시도 중..."
                    print(f"[{time.strftime('%H:%M:%S')}] {status_msg}")

            except asyncio.CancelledError:
                print("🩺 건강성 체크: 연결 끊어짐 감지")
                # CancelledError는 정상적인 종료 시그널이므로 재발생시켜 루프 종료
                raise

            except Exception as e:
                error_msg = f"⚠️ 서버 모니터링 오류: {e}"
                print(error_msg)
                await asyncio.sleep(5)
    
    async def setup_control_message_handler(self):
        """통합 제어 메시지 핸들러 설정 (운전 모드 관리자와 통합)"""
        if not self.logger or not self.mqtt_client:
            return
        
        async def integrated_message_callback(topic: str, payload: Dict[str, Any]):
            """통합 메시지 콜백 - 모든 MQTT 메시지를 받아서 적절한 핸들러로 라우팅"""
            try:
                print(f"📨 [통합핸들러] MQTT 메시지 수신: {topic}")
                print(f"📄 [통합핸들러] 메시지 내용: {payload}")
                
                # 1. 장비별 직접 제어 명령 처리 (/command로 끝나는 토픽)
                if '/control/' in topic and topic.endswith('/command'):
                    print(f"🎛️ [통합핸들러] 장비 제어 명령 감지: {topic}")
                    # 🔧 이미 파싱된 딕셔너리를 전달
                    await self.handle_control_command(topic, payload)
                    return
                
                # 2. 운전 모드 관리자가 처리해야 할 토픽들 (Location 기반)
                current_location = (self.config or {}).get('database', {}).get('device_location', 'unknown')
                operation_topics = [
                    f'pms/control/{current_location}/operation_mode',
                    f'pms/control/{current_location}/auto_mode/start',
                    f'pms/control/{current_location}/auto_mode/stop', 
                    f'pms/control/{current_location}/auto_mode/status',
                    f'pms/control/{current_location}/basic_mode',
                    f'pms/control/{current_location}/threshold_config'
                ]
                
                if topic in operation_topics:
                    print(f"🤖 [통합핸들러] 운전 모드 관리자로 전달: {topic}")
                    if self.operation_manager:
                        # 🔧 운전 모드 관리자는 JSON 문자열을 기대하므로 다시 직렬화
                        payload_str = json.dumps(payload, ensure_ascii=False)
                        self.operation_manager.handle_mqtt_message_threadsafe(topic, payload_str)
                    return
                
                # 3. 기타 메시지
                print(f"❓ [통합핸들러] 처리되지 않은 메시지: {topic}")
                
            except Exception as e:
                print(f"❌ [통합핸들러] 메시지 처리 오류: {e}")
                import traceback
                print(f"❌ 스택 트레이스:\n{traceback.format_exc()}")
        
        # MQTT 클라이언트에 통합 메시지 콜백 설정
        print("🔧 통합 메시지 콜백 설정 중...")
        self.mqtt_client.set_message_callback(integrated_message_callback)
        print("✅ 통합 메시지 콜백 설정 완료")
        
        # MQTT 연결 상태 재확인
        if not self.mqtt_client.is_connected():
            self.logger.error("❌ MQTT 클라이언트가 연결되지 않음 - 구독 실패 가능성")
            return
        
        self.logger.info("📡 제어 토픽 구독 시작...")
        
        # 모든 장비의 제어 토픽 구독
        for device_name in self.device_handler_map.keys():
            control_topic = f"pms/control/{device_name}/command"
            try:
                self.logger.info(f"📡 구독 시도: {control_topic}")
                await self.mqtt_client.subscribe(control_topic)
                self.logger.info(f"✅ 제어 토픽 구독 성공: {control_topic}")
            except Exception as e:
                self.logger.error(f"❌ 제어 토픽 구독 실패: {control_topic} - {e}")
        
        # 추가: 테스트 토픽도 구독해서 MQTT 메시지 수신이 작동하는지 확인
        test_topic = "pms/test/connection"
        try:
            self.logger.info(f"📡 테스트 토픽 구독 시도: {test_topic}")
            await self.mqtt_client.subscribe(test_topic)
            self.logger.info(f"✅ 테스트 토픽 구독 성공: {test_topic}")
        except Exception as e:
            self.logger.error(f"❌ 테스트 토픽 구독 실패: {test_topic} - {e}")
        
        self.logger.info("📡 모든 토픽 구독 완료")
    
    async def handle_control_command(self, topic: str, payload: Dict[str, Any]):
        """GUI에서 보낸 제어 명령 처리"""
        if not self.logger:
            return
        try:
            self.logger.info(f"🎯 제어 명령 처리 시작: {topic}")
            self.logger.info(f"📦 명령 페이로드: {payload}")
            
            # 토픽에서 장비 이름 추출: pms/control/{device_name}/command
            topic_parts = topic.split('/')
            if len(topic_parts) < 4 or topic_parts[0] != 'pms' or topic_parts[1] != 'control':
                self.logger.warning(f"❓ 잘못된 제어 토픽 형식: {topic}")
                return
            
            device_name = topic_parts[2]
            command_type = topic_parts[3]
            
            self.logger.info(f"🏷️ 장비명: {device_name}, 명령타입: {command_type}")
            
            if command_type != 'command':
                self.logger.warning(f"❓ 알 수 없는 명령 타입: {command_type}")
                return
            
            # 🔧 payload가 이미 딕셔너리로 파싱된 상태이므로 바로 사용
            command_data = payload
            self.logger.info(f"✅ 명령 데이터 확인: {command_data}")
            
            # 장비 핸들러 찾기
            self.logger.info(f"🔍 등록된 장비 목록: {list(self.device_handler_map.keys())}")
            
            if device_name not in self.device_handler_map:
                self.logger.error(f"❌ 알 수 없는 장비: {device_name}")
                await self.send_control_response(device_name, command_data.get("gui_request_id"), 
                                          False, f"알 수 없는 장비: {device_name}")
                return
            
            device_handler = self.device_handler_map[device_name]
            self.logger.info(f"✅ 장비 핸들러 찾음: {device_name} ({type(device_handler).__name__})")
            
            # 명령 실행
            action = command_data.get('action')
            self.logger.info(f"🎬 액션 실행: {action}")
            
            if action == 'write_register':
                self.logger.info(f"📝 레지스터 쓰기 명령 실행 중...")
                success = await self.execute_write_register(device_handler, command_data)
                
                self.logger.info(f"📊 명령 실행 결과: {'성공' if success else '실패'}")
                
                # 응답 전송
                response_msg = f"{'성공' if success else '실패'}: {command_data.get('description', '알 수 없는 명령')}"
                self.logger.info(f"📤 GUI 응답 준비: {response_msg}")
                
                await self.send_control_response(
                    device_name, 
                    command_data.get("gui_request_id"),
                    success,
                    response_msg
                )
            else:
                self.logger.warning(f"❓ 알 수 없는 액션: {action}")
                await self.send_control_response(device_name, command_data.get("gui_request_id"), 
                                          False, f"지원하지 않는 액션: {action}")
        
        except Exception as e:
            self.logger.error(f"❌ 제어 명령 처리 중 오류: {e}")
            self.logger.error(f"❌ 오류 세부사항: topic={topic}, payload={payload}")
            import traceback
            self.logger.error(f"❌ 스택 트레이스:\n{traceback.format_exc()}")
    
    async def execute_write_register(self, device_handler, command_data: Dict[str, Any]) -> bool:
        """레지스터 쓰기 명령 실행"""
        try:
            address = command_data.get('address')
            value = command_data.get('value')
            description = command_data.get('description', '레지스터 쓰기')
            
            # 로거가 None인 경우 print 사용
            def log_info(msg):
                if self.logger:
                    self.logger.info(msg)
                else:
                    print(msg)
            
            def log_error(msg):
                if self.logger:
                    self.logger.error(msg)
                else:
                    print(msg)
            
            if self.logger:
                self.logger.info(f"🔢 파라미터 추출: address={address}, value={value}, description={description}")
            
            # 타입 체크
            if address is None or value is None:
                if self.logger:
                    self.logger.error(f"❌ 필수 파라미터 누락: address={address}, value={value}")
                return False
            
            if not isinstance(address, int):
                try:
                    address = int(address)
                    if self.logger:
                        self.logger.info(f"🔄 주소 타입 변환: {address} (int)")
                except (ValueError, TypeError):
                    if self.logger:
                        self.logger.error(f"❌ 잘못된 주소 형식: {address}")
                    return False
            
            if self.logger:
                self.logger.info(f"🔧 제어 명령 실행: {device_handler.name} - {description} (주소: {address}, 값: {value}, HEX: 0x{value:04X})")
            
            # 주소를 통해 레지스터 이름 찾기
            if self.logger:
                self.logger.info(f"🔍 레지스터 이름 검색 시작: 주소 {address}")
            register_name = self.find_register_name_by_address(device_handler, address)
            
            if register_name:
                if self.logger:
                    self.logger.info(f"✅ 레지스터 이름 찾음: {register_name}")
                    self.logger.info(f"📝 Modbus write_register 호출 (타임아웃 10초): {register_name} = {value}")
                
                try:
                    import asyncio
                    result = await asyncio.wait_for(
                        device_handler.write_register(register_name, value),
                        timeout=10.0  # 10초 타임아웃
                    )
                    
                    if result:
                        if self.logger:
                            self.logger.info(f"✅ 제어 명령 성공: {device_handler.name} - {description}")
                        return True
                    else:
                        if self.logger:
                            self.logger.error(f"❌ 제어 명령 실패: {device_handler.name} - {description}")
                            self.logger.error(f"❌ write_register 반환값: {result}")
                        return False
                        
                except asyncio.TimeoutError:
                    if self.logger:
                        self.logger.error(f"⏱️ 제어 명령 타임아웃: {device_handler.name} - {description} (10초)")
                    return False
            else:
                if self.logger:
                    self.logger.error(f"❌ 레지스터 이름을 찾을 수 없음: 주소 {address}")
                    self.logger.error(f"❌ 사용 가능한 레지스터들을 확인하세요")
                return False
        
        except Exception as e:
            self.logger.error(f"❌ 레지스터 쓰기 실행 중 오류: {e}")
            self.logger.error(f"❌ 오류 파라미터: {command_data}")
            import traceback
            self.logger.error(f"❌ 스택 트레이스:\n{traceback.format_exc()}")
            return False
    
    def find_register_name_by_address(self, device_handler, address: int) -> Optional[str]:
        """주소로부터 레지스터 이름 찾기"""
        try:
            # 장비 타입별로 메모리 맵에서 레지스터 이름 검색
            memory_map = device_handler.device_map
            self.logger.info(f"📋 메모리 맵 섹션: {list(memory_map.keys())}")
            
            # 제어 레지스터에서 검색
            control_registers = memory_map.get('control_registers', {})
            self.logger.info(f"🎛️ 제어 레지스터 검색: {len(control_registers)}개 레지스터")
            
            for register_name, register_info in control_registers.items():
                reg_address = register_info.get('address')
                self.logger.debug(f"   📍 {register_name}: 주소 {reg_address}")
                if reg_address == address:
                    self.logger.info(f"✅ 제어 레지스터에서 찾음: {register_name} (주소: {address})")
                    return register_name
            
            # 다른 섹션에서도 검색 (파라미터 등)
            sections = ['parameter_registers', 'data_registers', 'metering_registers']
            for section in sections:
                section_data = memory_map.get(section, {})
                self.logger.info(f"📂 {section} 검색: {len(section_data)}개 레지스터")
                
                for register_name, register_info in section_data.items():
                    reg_address = register_info.get('address')
                    if reg_address == address:
                        self.logger.info(f"✅ {section}에서 찾음: {register_name} (주소: {address})")
                        return register_name
            
            self.logger.warning(f"❌ 주소 {address}에 해당하는 레지스터를 찾을 수 없음")
            return None
        
        except Exception as e:
            self.logger.error(f"❌ 레지스터 이름 검색 오류: {e}")
            return None
    
    async def send_control_response(self, device_name: str, request_id: Optional[str], success: bool, message: str):
        """제어 명령 응답 전송"""
        try:
            # 🔧 request_id가 None인 경우 기본값 사용
            if request_id is None:
                request_id = f"unknown_{device_name}_{int(time.time() * 1000)}"
            
            response_data = {
                "request_id": request_id,
                "success": success,
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "device_name": device_name
            }
            
            response_topic = f"pms/control/{device_name}/response"
            
            # 🔧 logger가 None인 경우 처리
            if self.logger:
                self.logger.info(f"📤 응답 데이터 준비: {response_data}")
                self.logger.info(f"📡 응답 토픽: {response_topic}")
            
            # MQTT publish 메소드 호출 (동기 함수)
            if self.mqtt_client:
                self.mqtt_client.publish(response_topic, response_data)
            
            if self.logger:
                self.logger.info(f"✅ 제어 응답 전송 완료: {device_name} - {message}")
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 제어 응답 전송 오류: {e}")
                self.logger.error(f"❌ 응답 데이터: {locals()}")
            else:
                print(f"❌ 제어 응답 전송 오류: {e}")
    
    def start_server_thread(self):
        """별도 스레드에서 PMS 서버 실행"""
        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start_pms_server())
            except Exception as e:
                print(f"❌ 서버 스레드 오류: {e}")
            finally:
                loop.close()
        
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        print("🔄 PMS 서버가 백그라운드 스레드에서 시작되었습니다.")
    
    async def _scheduler_recovery(self):
        """스케줄러 복구 콜백"""
        print("🔧 스케줄러 복구 실행 중...")
        try:
            if self.scheduler:
                # 스케줄러 상태 확인
                self.scheduler.log_status()
                
                # 비정상 장비 태스크 재시작
                stats = self.scheduler.get_all_stats()
                for device_name, device_stat in stats.get('device_stats', {}).items():
                    if not device_stat.get('is_healthy', True):
                        print(f"🔄 장비 태스크 재시작: {device_name}")
                        await self.scheduler.restart_device_task(device_name)
            
            print("✅ 스케줄러 복구 완료")
        except Exception as e:
            print(f"❌ 스케줄러 복구 실패: {e}")
    
    async def _mqtt_recovery(self):
        """MQTT 클라이언트 복구 콜백"""
        print("🔧 MQTT 클라이언트 복구 실행 중...")
        try:
            if self.mqtt_client:
                # MQTT 연결 상태 확인
                if not self.mqtt_client.is_connected():
                    print("⚠️ MQTT 연결 끊어짐 - 재연결 시도")
                    await self.mqtt_client.connect()
                
                # 발행 워커 상태 확인
                if hasattr(self.mqtt_client, 'publisher'):
                    publisher_stats = self.mqtt_client.publisher.get_stats()
                    if not publisher_stats.get('workers_running', False):
                        print("⚠️ MQTT 발행 워커 정지 - 재시작")
                        self.mqtt_client.publisher.start_workers()
            
            print("✅ MQTT 클라이언트 복구 완료")
        except Exception as e:
            print(f"❌ MQTT 클라이언트 복구 실패: {e}")
    
    async def _emergency_shutdown(self):
        """긴급 종료 핸들러"""
        print("🚨 긴급 종료 핸들러 실행")
        try:
            # 모든 구성 요소 정지
            if self.scheduler:
                await self.scheduler.stop()
                print("✅ 스케줄러 긴급 정지 완료")
            
            if self.mqtt_client:
                await self.mqtt_client.disconnect()
                print("✅ MQTT 클라이언트 긴급 정지 완료")
            
            if self.operation_manager:
                await self.operation_manager.shutdown()
                print("✅ 운전 모드 관리자 긴급 정지 완료")
            
            self.server_running = False
            print("✅ 긴급 종료 완료")
            
        except Exception as e:
            print(f"❌ 긴급 종료 실패: {e}")
    
    def stop_server(self):
        """PMS 서버 정지"""
        print("\n🛑 PMS 서버 정지 중...")
        self.server_running = False
        
        # 시스템 모니터 정지
        if self.system_monitor:
            try:
                def stop_system_monitor():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        if self.system_monitor:
                            loop.run_until_complete(self.system_monitor.stop())
                    finally:
                        loop.close()
                
                monitor_thread = threading.Thread(target=stop_system_monitor, daemon=True)
                monitor_thread.start()
                monitor_thread.join(timeout=3)  # 3초 대기
                print("✅ 시스템 모니터 정지 완료")
            except Exception as e:
                print(f"⚠️ 시스템 모니터 정지 중 오류: {e}")
        
        # 운전 모드 관리자 정지
        if self.operation_manager:
            try:
                # 운전 모드 관리자 종료는 별도 스레드에서 처리
                def shutdown_operation_manager():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        if self.operation_manager:
                            loop.run_until_complete(self.operation_manager.shutdown())
                    finally:
                        loop.close()
                
                shutdown_thread = threading.Thread(target=shutdown_operation_manager, daemon=True)
                shutdown_thread.start()
                shutdown_thread.join(timeout=5)  # 5초 대기
                print("✅ 운전 모드 관리자 정지 완료")
            except Exception as e:
                print(f"⚠️ 운전 모드 관리자 정지 중 오류: {e}")
        
        if self.scheduler:
            try:
                self.scheduler.shutdown()
                print("✅ 스케줄러 정지 완료")
            except AttributeError:
                print("⚠️ 스케줄러 shutdown 메서드 없음 - 수동 정지")
                self.scheduler.stop()
                print("✅ 스케줄러 정지 완료")
        
        if self.mqtt_client:
            try:
                # MQTT 연결 해제는 별도 스레드에서 처리
                def disconnect_mqtt():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        if self.mqtt_client:
                            loop.run_until_complete(self.mqtt_client.disconnect())
                    finally:
                        loop.close()
                
                disconnect_thread = threading.Thread(target=disconnect_mqtt)
                disconnect_thread.start()
                disconnect_thread.join(timeout=3)
                print("✅ MQTT 연결 해제 완료")
            except Exception as e:
                print(f"⚠️ MQTT 연결 해제 중 오류: {e}")
        
        print("🔴 PMS 서버 정지 완료")
    
    def run(self):
        """통합 애플리케이션 실행"""
        print("=" * 60)
        print("🎯 PMS 통합 애플리케이션 시작")
        print("  - GUI 인터페이스: 데이터 모니터링 및 제어")
        print("  - 백그라운드 서버: Modbus 폴링 및 MQTT 발행")
        print("=" * 60)
        
        # 1. 설정 로드
        if not self.load_config():
            print("❌ 설정 로드 실패로 종료합니다.")
            return
        
        # 2. 백그라운드 PMS 서버 시작
        self.start_server_thread()
        time.sleep(2)  # 서버 시작 대기
        
        # 3. GUI 시작
        print("\n🖥️ GUI 인터페이스 시작 중...")
        try:
            if self.config:
                self.gui_app = PMSMainWindow(self.config)
            else:
                raise ValueError("설정이 없어서 GUI를 시작할 수 없습니다")
            
            # GUI 종료 시 서버도 함께 정지
            original_on_closing = self.gui_app.on_closing
            def integrated_on_closing():
                self.stop_server()
                original_on_closing()
            
            self.gui_app.on_closing = integrated_on_closing
            
            print("✅ GUI 창이 열렸습니다.")
            print("💡 GUI 창을 닫으면 전체 애플리케이션이 종료됩니다.")
            
            # GUI 실행 (블로킹)
            self.gui_app.run()
            
        except Exception as e:
            print(f"❌ GUI 실행 오류: {e}")
            self.stop_server()
        
        print("\n👋 통합 애플리케이션 종료")


def main():
    """메인 함수"""
    try:
        app = IntegratedPMSApp()
        app.run()
    except KeyboardInterrupt:
        print("\n⚠️ 사용자에 의해 중단됨")
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main() 