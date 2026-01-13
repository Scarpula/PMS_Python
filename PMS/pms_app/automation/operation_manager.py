"""
운전 모드 관리자
PMS의 수동 운전 모드와 자동 운전 모드를 관리하고 MQTT 메시지를 처리합니다.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, TYPE_CHECKING
from enum import Enum

from .auto_mode import AutoModeController
from .auto_recovery import AutoRecoveryManager
from ..devices.base import DeviceInterface

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop


class OperationMode(Enum):
    """운전 모드"""
    BASIC = "basic"      # 수동 운전 모드
    AUTO = "auto"        # 자동 운전 모드


class OperationManager:
    """운전 모드 관리자"""
    
    def __init__(self, config: Dict[str, Any], device_handlers: Dict[str, DeviceInterface], mqtt_client, main_loop: 'AbstractEventLoop'):
        """
        운전 모드 관리자 초기화
        
        Args:
            config: 설정 딕셔너리
            device_handlers: 장비 핸들러 딕셔너리
            mqtt_client: MQTT 클라이언트
            main_loop: 메인 이벤트 루프
        """
        self.config = config
        self.device_handlers = device_handlers
        self.mqtt_client = mqtt_client
        self.main_loop = main_loop
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 현재 운전 모드
        self.current_mode = OperationMode.BASIC
        
        # 자동 운전 모드 제어기
        self.auto_controller = AutoModeController(config, device_handlers)

        # 자동 복구 관리자 (BMS/PCS 재시작 시 통신 에러 자동 복구)
        bms_handler = device_handlers.get('BMS')
        pcs_handler = device_handlers.get('PCS')

        if bms_handler and pcs_handler:
            self.auto_recovery = AutoRecoveryManager(bms_handler, pcs_handler)
            self.logger.info("🔧 자동 복구 관리자 활성화")
        else:
            self.auto_recovery = None
            self.logger.warning("⚠️ BMS 또는 PCS 핸들러가 없어 자동 복구 비활성화")

        # MQTT 토픽 설정
        self.control_topics = self._setup_control_topics()

        # 실행 상태 관리
        self.is_running = False
        self.threshold_status_task = None
        self.auto_recovery_task = None

        self.logger.info("운전 모드 관리자 초기화 완료")
    
    def _setup_control_topics(self) -> Dict[str, str]:
        """제어 토픽 설정 (Location 기반)"""
        base_topic = self.config.get('mqtt', {}).get('base_topic', 'pms')
        location = self.config.get('database', {}).get('device_location', 'unknown')
        
        topics = {
            'operation_mode': f"{base_topic}/control/{location}/operation_mode",
            'auto_start': f"{base_topic}/control/{location}/auto_mode/start",
            'auto_stop': f"{base_topic}/control/{location}/auto_mode/stop",
            'auto_status': f"{base_topic}/control/{location}/auto_mode/status",
            'manual_control': f"{base_topic}/control/{location}/basic_mode",
            'threshold_config': f"{base_topic}/control/{location}/threshold_config",
            'status': f"{base_topic}/status/{location}/operation_mode",
            'threshold_status': f"{base_topic}/status/{location}/threshold_config"
        }
        
        self.logger.info(f"🏷️ Location 기반 토픽 설정: {location}")
        self.logger.info(f"📡 제어 토픽 목록: {list(topics.values())}")
        
        return topics
    
    async def initialize(self):
        """운전 모드 관리자 초기화"""
        try:
            self.logger.info("🚀 운전 모드 관리자 초기화 시작")
            
            # MQTT 제어 토픽 구독
            for topic_name, topic in self.control_topics.items():
                if topic_name in ['operation_mode', 'auto_start', 'auto_stop', 'auto_status', 'manual_control', 'threshold_config']:
                    success = await self.mqtt_client.subscribe(topic)
                    if success:
                        self.logger.info(f"✅ 제어 토픽 구독 성공: {topic}")
                    else:
                        self.logger.error(f"❌ 제어 토픽 구독 실패: {topic}")
            
            # MQTT 메시지 콜백은 통합 핸들러에서 관리하므로 여기서는 설정하지 않음
            # (통합 PMS 앱의 integrated_message_callback에서 이 클래스의 핸들러를 호출함)
            # self.mqtt_client.set_message_callback(self.handle_mqtt_message_threadsafe)
            
            # MQTT 상태 점검
            self.mqtt_client.log_status()
            
            # 초기 상태 발행
            await self._publish_status()
            
            # 실행 상태 설정 및 주기적 임계값 상태 전송 시작
            self.is_running = True
            self.threshold_status_task = asyncio.create_task(self._send_periodic_threshold_status())

            # 자동 복구 감시 태스크 시작
            if self.auto_recovery:
                self.auto_recovery_task = asyncio.create_task(self._auto_recovery_monitor())
                self.logger.info("🔧 자동 복구 감시 태스크 시작")

            # 초기 임계값 상태 전송
            await self._publish_threshold_status()

            self.logger.info("✅ 운전 모드 관리자 초기화 완료")
            
        except Exception as e:
            self.logger.error(f"❌ 운전 모드 관리자 초기화 실패: {e}")
            raise
    
    def handle_mqtt_message_threadsafe(self, topic: str, payload: str):
        """
        MQTT 메시지를 스레드 안전하게 처리합니다.
        이 함수는 MQTT 클라이언트의 네트워크 스레드에서 직접 호출됩니다.
        """
        try:
            self.logger.info(f"🎯 [ThreadSafe] MQTT 메시지 수신 - Topic: {topic}")
            
            # JSON 파싱
            message = json.loads(payload)
            
            # 토픽에 따라 적절한 비동기 핸들러를 메인 루프에서 실행
            if topic == self.control_topics['operation_mode']:
                coro = self._async_handle_operation_mode(message)
            elif topic == self.control_topics['auto_start']:
                coro = self._async_handle_auto_start(message)
            elif topic == self.control_topics['auto_stop']:
                coro = self._async_handle_auto_stop(message)
            elif topic == self.control_topics['auto_status']:
                coro = self._async_handle_auto_status(message)
            elif topic == self.control_topics['manual_control']:
                coro = self._async_handle_manual_control(message)
            elif topic == self.control_topics['threshold_config']:
                coro = self._async_handle_threshold_config(message)
            elif '/command' in topic and topic.startswith('pms/control/'):
                # 장비별 직접 제어 명령은 운전 모드 관리자에서 처리하지 않음
                # 메인 애플리케이션의 제어 핸들러에서 처리됨
                self.logger.info(f"🎮 장비 제어 명령 감지 (메인 핸들러에서 처리): {topic}")
                return
            else:
                self.logger.warning(f"❓ 알 수 없는 제어 토픽: {topic}")
                return
            
            # 메인 이벤트 루프에서 코루틴 실행 예약
            asyncio.run_coroutine_threadsafe(coro, self.main_loop)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"   ❌ JSON 파싱 실패: {e}")
        except Exception as e:
            self.logger.error(f"❌ MQTT 메시지 스케줄링 중 오류: {e}")

    def _check_message_location(self, message: Dict[str, Any]) -> bool:
        """메시지의 location이 현재 시스템과 일치하는지 확인"""
        message_location = message.get('location')
        current_location = self.config.get('database', {}).get('device_location')
        
        # location 정보가 없으면 호환성을 위해 처리 허용
        if not message_location:
            self.logger.info(f"📍 메시지에 location 정보 없음 - 처리 허용 (호환성)")
            return True
        
        if not current_location:
            self.logger.warning(f"⚠️ 현재 시스템 device_location 설정 없음 - 처리 허용")
            return True
        
        is_match = message_location == current_location
        
        if is_match:
            self.logger.info(f"✅ Location 일치: {message_location} - 메시지 처리")
        else:
            self.logger.info(f"❌ Location 불일치: 메시지({message_location}) ≠ 시스템({current_location}) - 메시지 무시")
        
        return is_match

    async def _async_handle_operation_mode(self, message: Dict[str, Any]):
        """(Async) 운전 모드 변경 메시지 처리"""
        self.logger.info(f"🔄 [Async] 운전 모드 변경 처리 시작: {message}")
        
        # Location 필터링 확인
        if not self._check_message_location(message):
            return
        
        mode_str = message.get('mode', '').lower()
        
        if mode_str == 'basic':
            await self.set_manual_mode()
        elif mode_str == 'auto':
            await self.set_auto_mode()
        else:
            self.logger.error(f"❌ 지원하지 않는 운전 모드: '{mode_str}'")
            await self._publish_error(f"Unsupported operation mode: {mode_str}")
        
        await self._publish_status()
        await self._publish_threshold_status()

    async def _async_handle_auto_start(self, message: Dict[str, Any]):
        """(Async) 자동 모드 시작 메시지 처리"""
        self.logger.info(f"🚀 [Async] 자동 모드 시작 처리")
        
        # Location 필터링 확인
        if not self._check_message_location(message):
            return
        
        try:
            if self.current_mode != OperationMode.AUTO:
                await self.set_auto_mode()
            
            success = await self.auto_controller.start_auto_mode()
            
            response = {
                'command': 'auto_start',
                'success': success,
                'timestamp': self.main_loop.time(),
                'message': '자동 운전 모드 시작됨' if success else '자동 운전 모드 시작 실패 - 장비 연결을 확인하세요',
                'auto_mode_status': self.auto_controller.get_status(),
                'troubleshooting': [] if success else [
                    "네트워크 케이블 연결 확인",
                    "PCS/BMS 장비 전원 상태 확인", 
                    "IP 주소 설정 확인"
                ]
            }
            await self._publish_response(response)
        except Exception as e:
            self.logger.error(f"❌ 자동 모드 시작 처리 중 오류: {e}", exc_info=True)
            response = {
                'command': 'auto_start',
                'success': False,
                'timestamp': self.main_loop.time(),
                'message': f'자동 모드 시작 중 오류 발생: {str(e)}',
                'error_type': type(e).__name__
            }
            await self._publish_response(response)
        
        await self._publish_status()

    async def _async_handle_auto_stop(self, message: Dict[str, Any]):
        """(Async) 자동 모드 정지 메시지 처리"""
        self.logger.info(f"🛑 [Async] 자동 모드 정지 처리")
        
        # Location 필터링 확인
        if not self._check_message_location(message):
            return
        
        success = await self.auto_controller.stop_auto_mode()
        
        response = {
            'command': 'auto_stop',
            'success': success,
            'timestamp': self.main_loop.time(),
            'message': '자동 운전 모드 정지됨' if success else '자동 운전 모드 정지 실패',
            'auto_mode_status': self.auto_controller.get_status()
        }
        await self._publish_response(response)
        await self._publish_status()

    async def _async_handle_auto_status(self, message: Dict[str, Any]):
        """(Async) 자동 모드 상태 조회 메시지 처리"""
        # Location 필터링 확인
        if not self._check_message_location(message):
            return
            
        status = self.get_status()
        await self._publish_response(status)

    async def _async_handle_manual_control(self, message: Dict[str, Any]):
        """(Async) 수동 모드 제어 메시지 처리"""
        self.logger.info(f"🎮 [Async] 수동 모드 제어 처리")
        
        # Location 필터링 확인
        if not self._check_message_location(message):
            return
        
        if self.current_mode == OperationMode.AUTO:
            self.logger.warning("자동 모드 중에는 수동 제어를 할 수 없습니다. 먼저 수동 모드로 전환하세요.")
            await self._publish_error("Cannot perform manual control in AUTO mode.")
            return

        device_name = message.get('device_name')
        command = message.get('command')
        params = message.get('params', {})
        
        if not device_name or not command:
            await self._publish_error("Missing 'device_name' or 'command'.")
            return
        
        handler = self.device_handlers.get(device_name)
        if not handler:
            await self._publish_error(f"Device '{device_name}' not found.")
            return

        if hasattr(handler, 'handle_control_message'):
            await handler.handle_control_message({'command': command, 'params': params})
        else:
            await self._publish_error(f"Device '{device_name}' does not support direct control.")

    async def _async_handle_threshold_config(self, message: Dict[str, Any]):
        """(Async) 임계값 설정 메시지 처리"""
        self.logger.info(f"⚙️ [Async] 임계값 설정 처리")
        
        # Location 필터링 확인
        if not self._check_message_location(message):
            return
        
        try:
            success, result_message = self.auto_controller.state_machine.update_thresholds(message)
            
            response = {
                'command': 'threshold_config',
                'success': success,
                'timestamp': self.main_loop.time(),
                'message': result_message
            }
            await self._publish_response(response)
            
            # 변경된 임계값 상태 즉시 전송
            await self._publish_threshold_status()

        except Exception as e:
            self.logger.error(f"❌ 임계값 설정 처리 중 오류: {e}", exc_info=True)
            await self._publish_error(f"Error processing thresholds: {e}")

    async def set_manual_mode(self):
        """수동 운전 모드로 설정"""
        self.logger.info("🔧 수동 운전 모드로 전환합니다.")
        
        response_msg = "수동 운전 모드로 전환되었습니다."
        
        if self.current_mode == OperationMode.AUTO:
            self.logger.info("... 자동 운전 모드를 정지합니다.")
            stop_success = await self.auto_controller.stop_auto_mode()
            if not stop_success:
                self.logger.warning("⚠️ 자동 운전 모드 정지에 실패했지만, 강제로 수동 모드로 전환합니다.")
                response_msg = "자동 모드 정지 실패. 강제로 수동 모드로 전환되었습니다."

        self.current_mode = OperationMode.BASIC
        self.logger.info("✅ 현재 모드: 수동")
        
        # 상태 발행
        await self._publish_status()
        
        # 전환 성공 응답 발행
        response = {
            'command': 'set_mode_basic',
            'success': True,
            'timestamp': self.main_loop.time(),
            'message': response_msg,
            'current_mode': self.current_mode.value
        }
        await self._publish_response(response)
    
    async def set_auto_mode(self):
        """자동 운전 모드로 설정"""
        self.logger.info("🤖 자동 운전 모드로 전환합니다.")
        
        auto_mode_enabled = self.config.get('auto_mode', {}).get('enabled', False)
        if not auto_mode_enabled:
            self.logger.warning("자동 운전 모드가 비활성화되어 있습니다.")
            await self._publish_error("Auto mode is disabled in the configuration.")
            return

        self.current_mode = OperationMode.AUTO
        self.logger.info("✅ 현재 모드: 자동")
        
        # 상태 발행
        await self._publish_status()
        
        # 전환 성공 응답 발행
        response = {
            'command': 'set_mode_auto',
            'success': True,
            'timestamp': self.main_loop.time(),
            'message': "자동 운전 모드로 전환되었습니다.",
            'current_mode': self.current_mode.value
        }
        await self._publish_response(response)
    
    async def _publish_status(self):
        """현재 상태 발행"""
        status = self.get_status()
        # location 정보 추가
        status = self._add_location_to_message(status)
        
        if self.mqtt_client.is_connected():
            self.mqtt_client.publish(self.control_topics['status'], status)
    
    async def _publish_response(self, response: Dict[str, Any]):
        """응답 메시지 발행"""
        response_topic = f"{self.control_topics['status']}/response"
        
        # 응답에 location 정보 추가
        response = self._add_location_to_message(response)
        
        if self.mqtt_client.is_connected():
            self.mqtt_client.publish(response_topic, response)
    
    async def _publish_error(self, error_message: str):
        """오류 메시지 발행"""
        error_response = {
            'error': True,
            'message': error_message,
            'timestamp': self.main_loop.time()
        }
        
        await self._publish_response(error_response)
    
    def get_status(self) -> Dict[str, Any]:
        """운전 모드 상태 정보"""
        status = {
            'current_mode': self.current_mode.value,
            'timestamp': self.main_loop.time(),
            'manual_mode': {
                'active': self.current_mode == OperationMode.BASIC,
                'available_devices': list(self.device_handlers.keys())
            }
        }
        
        # 자동 모드 상태 추가 - 더 정확한 상태 정보 제공
        auto_status = self.auto_controller.get_status()
        
        # 자동 모드 활성 상태는 상태 머신의 is_auto_mode_active()로 판단
        is_auto_active = self.auto_controller.is_auto_mode_active()
        
        status['auto_mode'] = {
            'active': is_auto_active,
            'available': True,
            'current_state': auto_status['auto_mode']['current_state'],
            'state_duration_seconds': auto_status['auto_mode']['state_duration_seconds'],
            'config': auto_status['auto_mode']['config'],
            'last_soc': auto_status.get('last_soc', 0),
            'devices': auto_status.get('devices', {})
        }
        
        return status
    
    def get_control_topics(self) -> Dict[str, str]:
        """제어 토픽 목록 반환"""
        return self.control_topics.copy()
    
    def _get_current_location(self) -> str:
        """현재 시스템의 device_location 반환"""
        return self.config.get('database', {}).get('device_location', 'Unknown')

    def _add_location_to_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """메시지에 location 정보 추가"""
        message['location'] = self._get_current_location()
        return message

    async def _publish_threshold_status(self):
        """현재 임계값 설정 상태를 전송"""
        try:
            self.logger.info(f"📊 [임계값 상태] 전송 시작")
            
            # 현재 상태 머신에서 설정 정보 가져오기
            config = self.auto_controller.state_machine.get_status()['config']
            
            threshold_status = {
                'type': 'threshold_config',
                'timestamp': self.main_loop.time(),
                'soc_high_threshold': config['soc_high_threshold'],
                'soc_low_threshold': config['soc_low_threshold'],
                'soc_charge_stop_threshold': config['soc_charge_stop_threshold'],
                'dcdc_standby_time': config['dcdc_standby_time'],
                'charging_power': config['charging_power'],
                'operation_mode': self.current_mode.value,
                'auto_mode_status': self.auto_controller.state_machine.current_state.value if self.current_mode == OperationMode.AUTO else 'IDLE'
            }
            
            # location 정보 추가
            threshold_status = self._add_location_to_message(threshold_status)
            
            # threshold_status 토픽으로 발행
            topic = self.control_topics['threshold_status']
            
            self.logger.info(f"📤 임계값 상태 발행")
            self.logger.info(f"   📡 토픽: {topic}")
            self.logger.info(f"   📄 상태: {threshold_status}")
            
            if self.mqtt_client and self.mqtt_client.is_connected():
                self.mqtt_client.publish(topic, threshold_status)
                self.logger.info(f"✅ 임계값 상태 전송 완료")
            else:
                self.logger.warning("⚠️ MQTT 클라이언트가 연결되지 않음")
                
        except Exception as e:
            self.logger.error(f"❌ 임계값 상태 전송 중 오류: {e}")
            import traceback
            self.logger.error(f"📍 스택 트레이스:\n{traceback.format_exc()}")

    async def _send_periodic_threshold_status(self):
        """주기적으로 임계값 상태를 전송 (30초마다)"""
        self.logger.info(f"🔄 주기적 임계값 상태 전송 시작 (30초 간격)")

        while self.is_running:
            try:
                await self._publish_threshold_status()
                await asyncio.sleep(30)  # 30초 간격
            except asyncio.CancelledError:
                self.logger.info(f"🛑 주기적 임계값 상태 전송 중단됨")
                break
            except Exception as e:
                self.logger.error(f"❌ 주기적 임계값 상태 전송 중 오류: {e}")
                await asyncio.sleep(30)

    async def _auto_recovery_monitor(self):
        """
        BMS Communication Error 자동 복구 감시 (30초마다 체크)

        PMS가 오랫동안 꺼졌다가 켜질 경우 BMS에 Communication Error가 발생할 수 있습니다.
        이 에러를 감지하면 자동으로 복구 시퀀스를 실행합니다.
        """
        self.logger.info("🔍 자동 복구 감시 시작 (30초 간격)")

        # 초기 대기 시간 (시스템 안정화)
        await asyncio.sleep(10)

        while self.is_running:
            try:
                # BMS 핸들러 확인
                bms_handler = self.device_handlers.get('BMS')
                if not bms_handler:
                    self.logger.debug("BMS 핸들러가 없어 자동 복구 감시 스킵")
                    await asyncio.sleep(30)
                    continue

                # BMS가 연결되어 있지 않으면 스킵
                if not bms_handler.connected:
                    self.logger.debug("BMS가 연결되지 않아 자동 복구 감시 스킵")
                    await asyncio.sleep(30)
                    continue

                # BMS 데이터 읽기
                bms_data = await bms_handler.read_data()

                if bms_data:
                    # 자동 복구 확인 및 실행
                    recovery_attempted = await self.auto_recovery.check_and_recover(bms_data)

                    if recovery_attempted:
                        # 복구 시도 후 추가 대기 시간 (시스템 안정화)
                        self.logger.info("⏳ 복구 후 시스템 안정화 대기 (60초)")
                        await asyncio.sleep(60)
                        continue

                # 정상 간격으로 대기
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                self.logger.info("🛑 자동 복구 감시 중단됨")
                break
            except Exception as e:
                self.logger.error(f"❌ 자동 복구 감시 중 오류: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def shutdown(self):
        """운전 모드 관리자 종료"""
        self.logger.info("운전 모드 관리자 종료 중...")
        
        try:
            # 실행 상태 변경
            self.is_running = False

            # 주기적 임계값 상태 전송 태스크 정지
            if self.threshold_status_task:
                self.threshold_status_task.cancel()
                try:
                    await self.threshold_status_task
                except asyncio.CancelledError:
                    pass

            # 자동 복구 감시 태스크 정지
            if self.auto_recovery_task:
                self.auto_recovery_task.cancel()
                try:
                    await self.auto_recovery_task
                except asyncio.CancelledError:
                    pass
            
            # 자동 모드 정지
            if self.auto_controller.is_auto_mode_active():
                await self.auto_controller.stop_auto_mode()
            
            # MQTT 토픽 구독 해제
            for topic in self.control_topics.values():
                try:
                    await self.mqtt_client.unsubscribe(topic)
                except:
                    pass
            
            self.logger.info("운전 모드 관리자 종료 완료")
            
        except Exception as e:
            self.logger.error(f"운전 모드 관리자 종료 중 오류: {e}") 