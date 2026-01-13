"""
자동 운전 모드 제어기
PMS 자동 운전 모드의 실제 제어 로직을 구현합니다.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from .state_machine import AutoModeStateMachine, AutoModeState
from ..devices.base import DeviceInterface
from ..core.data_manager import data_manager


class AutoModeController:
    """자동 운전 모드 제어기"""
    
    def __init__(self, config: Dict[str, Any], device_handlers: Dict[str, DeviceInterface]):
        """
        자동 운전 모드 제어기 초기화
        
        Args:
            config: 설정 딕셔너리
            device_handlers: 장비 핸들러 딕셔너리
        """
        self.config = config
        self.device_handlers = device_handlers
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 상태 머신 초기화
        self.state_machine = AutoModeStateMachine(config)
        self.state_machine.add_state_change_callback(self._on_state_change)
        
        # 장비 핸들러 참조 (실제 핸들러 타입으로 저장)
        self.pcs_handler = None
        self.dcdc_handler = None
        self.bms_handler = None
        
        self._find_device_handlers()
        
        # SOC 모니터링
        self.last_soc_value = 0.0
        self.soc_update_interval = config.get('auto_mode', {}).get('soc_monitor_interval', 2.0)
        self.soc_monitor_task = None
        
        # 충전 제어 (state_machine에서 동적으로 관리)
        
        self.logger.info("자동 운전 모드 제어기 초기화 완료")
    
    def _find_device_handlers(self):
        """장비 핸들러 찾기"""
        for name, handler in self.device_handlers.items():
            if handler.__class__.__name__ == 'PCSHandler':
                self.pcs_handler = handler
                self.logger.info(f"PCS 핸들러 발견: {name}")
            elif handler.__class__.__name__ == 'DCDCHandler':
                self.dcdc_handler = handler
                self.logger.info(f"DCDC 핸들러 발견: {name}")
            elif handler.__class__.__name__ == 'BMSHandler':
                self.bms_handler = handler
                self.logger.info(f"BMS 핸들러 발견: {name}")
    
    async def start_auto_mode(self) -> bool:
        """자동 운전 모드 시작"""
        self.logger.info("🚀 자동 운전 모드 시작 요청")
        
        # 필요한 장비 확인
        self.logger.info("🔍 필요한 장비 확인 중...")
        if not self._check_required_devices():
            self.logger.error("❌ 필요한 장비가 없어 자동 모드를 시작할 수 없습니다")
            return False
        self.logger.info("✅ 필요한 장비 확인 완료")
        
        # SOC 모니터링 시작
        self.logger.info("📊 SOC 모니터링 시작 중...")
        await self._start_soc_monitoring()
        
        # 상태 머신 시작
        self.logger.info("🎛️ 상태 머신 시작 중...")
        success = await self.state_machine.start_auto_mode()
        
        if success:
            current_state = self.state_machine.get_current_state()
            self.logger.info(f"✅ 자동 운전 모드 시작됨 - 현재 상태: {current_state.value}")
            self.logger.info(f"🔄 자동 모드 활성 상태: {self.is_auto_mode_active()}")
        else:
            self.logger.error("❌ 자동 운전 모드 시작 실패")
            await self._stop_soc_monitoring()
        
        return success
    
    async def stop_auto_mode(self) -> bool:
        """자동 운전 모드 정지"""
        self.logger.info("자동 운전 모드 정지 요청")
        
        # SOC 모니터링 정지
        await self._stop_soc_monitoring()
        
        # 상태 머신 정지
        success = await self.state_machine.stop_auto_mode()
        
        if success:
            self.logger.info("자동 운전 모드 정지됨")
        else:
            self.logger.error("자동 운전 모드 정지 실패")
        
        return success
    
    def _check_required_devices(self) -> bool:
        """필요한 장비 확인"""
        missing_devices = []
        
        if not self.pcs_handler:
            missing_devices.append("PCS")
        if not self.bms_handler:
            missing_devices.append("BMS")
        # DCDC는 선택적으로 사용 (없어도 동작 가능)
        
        if missing_devices:
            self.logger.error(f"필요한 장비를 찾을 수 없습니다: {', '.join(missing_devices)}")
            return False
        
        return True
    
    async def _start_soc_monitoring(self):
        """SOC 모니터링 시작"""
        if self.soc_monitor_task and not self.soc_monitor_task.done():
            return
        
        self.logger.info("SOC 모니터링 시작")
        self.soc_monitor_task = asyncio.create_task(self._soc_monitor_loop())
    
    async def _stop_soc_monitoring(self):
        """SOC 모니터링 정지 (안정성 강화)"""
        if self.soc_monitor_task and not self.soc_monitor_task.done():
            self.logger.info("SOC 모니터링 태스크를 취소합니다...")
            self.soc_monitor_task.cancel()
            try:
                await self.soc_monitor_task
            except asyncio.CancelledError:
                self.logger.info("✅ SOC 모니터링 태스크가 정상적으로 취소되었습니다.")
            except Exception as e:
                # 태스크가 다른 루프에 속해있거나 이미 종료된 경우 등 예외 처리
                self.logger.error(f"⚠️ SOC 모니터링 태스크 정리 중 예상치 못한 오류 발생: {e}", exc_info=False)
        
        self.soc_monitor_task = None
        self.logger.info("SOC 모니터링이 정지되었습니다.")
    
    async def _soc_monitor_loop(self):
        """SOC 모니터링 루프 - 폴링 데이터에서 SOC 값 읽기"""
        consecutive_failures = 0
        max_failures = 5  # 5회 연속 실패 시 경고
        
        while True:
            try:
                if self.bms_handler:
                    # 데이터 매니저에서 폴링된 BMS 데이터 읽기
                    bms_data = data_manager.get_device_data(self.bms_handler.name)
                    
                    if bms_data and 'data' in bms_data:
                        raw_data = bms_data['data']
                        soc_found = False
                        
                        # 1차: battery_soc 필드 확인
                        if 'battery_soc' in raw_data:
                            soc_raw = raw_data['battery_soc']
                            soc_value = soc_raw * 0.1  # scale 적용
                            soc_found = True
                            self.logger.debug(f"📊 SOC 데이터 확인: {soc_value:.1f}% (raw: {soc_raw})")
                        
                        # 2차: 가공된 데이터에서 battery_soc 확인
                        elif 'processed_data' in bms_data and 'battery_soc' in bms_data['processed_data']:
                            processed_soc = bms_data['processed_data']['battery_soc']
                            if isinstance(processed_soc, dict) and 'value' in processed_soc:
                                soc_value = processed_soc['value']
                                soc_found = True
                                self.logger.debug(f"📊 SOC 가공 데이터 확인: {soc_value:.1f}%")
                        
                        # 3차: 다른 SOC 관련 필드 확인 (fallback)
                        else:
                            for key in raw_data.keys():
                                if 'soc' in key.lower():
                                    self.logger.debug(f"🔍 대체 SOC 필드 발견: {key} = {raw_data[key]}")
                                    soc_value = raw_data[key] * 0.1  # 기본 스케일 적용
                                    soc_found = True
                                    break
                        
                        if soc_found:
                            # SOC 값 유효성 검사
                            if 0 <= soc_value <= 100:
                                # SOC 값이 변경되면 상태 머신에 알림
                                if abs(soc_value - self.last_soc_value) > 0.1:  # 0.1% 이상 변화
                                    self.logger.info(f"🔋 SOC 업데이트: {self.last_soc_value:.1f}% -> {soc_value:.1f}%")
                                    await self.state_machine.trigger_event('soc_update', {'soc': soc_value})
                                    self.last_soc_value = soc_value
                                
                                consecutive_failures = 0  # 성공 시 실패 카운터 리셋
                            else:
                                self.logger.warning(f"⚠️ SOC 값이 유효 범위(0-100%)를 벗어남: {soc_value:.1f}%")
                                consecutive_failures += 1
                        else:
                            consecutive_failures += 1
                            self.logger.warning(f"⚠️ 폴링 데이터에 battery_soc 필드 없음 (연속 실패: {consecutive_failures}/{max_failures})")
                            
                            # 디버깅을 위한 상세 로그
                            if consecutive_failures <= 3:  # 처음 3회만 상세 로그
                                available_keys = list(raw_data.keys())
                                self.logger.debug(f"🔍 사용 가능한 BMS 데이터 키: {available_keys[:10]}...")  # 처음 10개만 표시
                    else:
                        consecutive_failures += 1
                        self.logger.warning(f"⚠️ BMS 폴링 데이터 없음 - 연결 상태 확인 필요 (연속 실패: {consecutive_failures}/{max_failures})")
                        
                        # 데이터 매니저 상태 확인
                        if consecutive_failures == 1:  # 첫 실패 시에만 상세 로그
                            all_devices = data_manager.get_all_device_names()
                            self.logger.debug(f"🔍 데이터 매니저에 등록된 장비: {all_devices}")
                
                # 연속 실패 경고
                if consecutive_failures >= max_failures:
                    self.logger.error(f"❌ SOC 데이터 수신 {max_failures}회 연속 실패 - Modbus 연결 또는 폴링 상태 확인 필요")
                    self.logger.error("🔧 해결 방법:")
                    self.logger.error("   1. BMS 장비 연결 상태 확인")
                    self.logger.error("   2. Modbus 통신 설정 확인")
                    self.logger.error("   3. 폴링 스케줄러 상태 확인")
                    consecutive_failures = 0  # 경고 후 카운터 리셋하여 반복 방지
                
                await asyncio.sleep(self.soc_update_interval)
                
            except asyncio.CancelledError:
                self.logger.info("🛑 SOC 모니터링 루프 종료됨")
                break
            except Exception as e:
                consecutive_failures += 1
                self.logger.error(f"❌ SOC 모니터링 중 예외 발생: {e}", exc_info=True)
                await asyncio.sleep(self.soc_update_interval)
    
    async def _on_state_change(self, previous_state: AutoModeState, current_state: AutoModeState, trigger: str):
        """상태 변경 시 호출되는 콜백"""
        self.logger.info(f"자동 운전 모드 상태 변경: {previous_state.value} -> {current_state.value}")
        
        # 각 상태에 따른 제어 실행
        try:
            if current_state == AutoModeState.INITIALIZING:
                await self._handle_initializing()
            elif current_state == AutoModeState.PCS_STANDBY:
                await self._handle_pcs_standby()
            elif current_state == AutoModeState.PCS_INVERTER:
                await self._handle_pcs_inverter()
            elif current_state == AutoModeState.DCDC_RESET:
                await self._handle_dcdc_reset()
            elif current_state == AutoModeState.DCDC_SOLAR:
                await self._handle_dcdc_solar()
            elif current_state == AutoModeState.SOC_HIGH_WAIT:
                await self._handle_soc_high_wait()
            elif current_state == AutoModeState.SOC_LOW_CHARGING:
                await self._handle_soc_low_charging()
            elif current_state == AutoModeState.NORMAL_OPERATION:
                await self._handle_normal_operation()
            elif current_state == AutoModeState.STOPPING:
                await self._handle_stopping()
                
        except Exception as e:
            self.logger.error(f"상태 처리 중 오류 발생: {e}")
            await self.state_machine.trigger_event('error')
    
    async def _handle_initializing(self):
        """초기화 상태 처리"""
        self.logger.info("자동 운전 모드 초기화 중...")
        
        # 필수 장비 핸들러 확인
        devices_ready = True
        missing_handlers = []
        
        if not self.pcs_handler:
            missing_handlers.append("PCS")
            devices_ready = False
        else:
            self.logger.info("✅ PCS 핸들러 확인됨")
        
        if not self.bms_handler:
            missing_handlers.append("BMS")
            devices_ready = False
        else:
            self.logger.info("✅ BMS 핸들러 확인됨")
        
        if self.dcdc_handler:
            self.logger.info("✅ DCDC 핸들러 확인됨")
        else:
            self.logger.info("ℹ️ DCDC 핸들러 없음 (선택사항)")

        if devices_ready:
            self.logger.info("✅ 필수 장비 핸들러 모두 확인됨. 초기화 완료.")
            await self.state_machine.trigger_event('init_complete')
        else:
            self.logger.error(f"❌ 필수 장비 핸들러 없음: {', '.join(missing_handlers)}")
            self.logger.error("🔧 해결 방법:")
            self.logger.error("   1. 장비 설정 파일(config.yml) 확인")
            self.logger.error("   2. 장비 맵 파일(pcs_map.json 등) 존재 확인") 
            self.logger.error("   3. PMS 재시작 후 재시도")
            await self.state_machine.trigger_event('error')
    
    async def _handle_pcs_standby(self):
        """PCS 대기 모드 처리"""
        self.logger.info("PCS 대기 모드 실행")
        
        if self.pcs_handler:
            # PCS Standby Start (21)
            success = await self.pcs_handler.write_register('pcs_standby_start', 85)
            if success:
                self.logger.info("PCS 대기 모드 명령 전송 완료")
                # 5초 후 자동으로 다음 상태로 전환 (state_machine에서 처리)
            else:
                self.logger.error("PCS 대기 모드 명령 전송 실패")
                await self.state_machine.trigger_event('error')
    
    async def _handle_pcs_inverter(self):
        """PCS 독립 운전 모드 처리"""
        self.logger.info("PCS 독립 운전 모드 실행")
        
        if self.pcs_handler:
            # Inverter Start Mode (24) - 독립운전 Option
            success = await self.pcs_handler.write_register('inv_start_mode', 85)
            if success:
                self.logger.info("PCS 독립 운전 모드 명령 전송 완료")
                await self.state_machine.trigger_event('pcs_ready')
            else:
                self.logger.error("PCS 독립 운전 모드 명령 전송 실패")
                await self.state_machine.trigger_event('error')
    
    async def _handle_dcdc_reset(self):
        """DCDC 리셋 처리"""
        self.logger.info("DCDC 리셋 실행")
        
        if self.dcdc_handler:
            # DCDC Reset Command (100)
            success = await self.dcdc_handler.write_register('reset_command', 85)
            if success:
                self.logger.info("DCDC 리셋 명령 전송 완료")
                # 5초 후 자동으로 다음 상태로 전환 (state_machine에서 처리)
            else:
                self.logger.error("DCDC 리셋 명령 전송 실패")
                await self.state_machine.trigger_event('error')
        else:
            self.logger.warning("DCDC 핸들러가 없습니다. DCDC 단계를 건너뜁니다.")
            await self.state_machine.trigger_event('dcdc_ready')
    
    async def _handle_dcdc_solar(self):
        """DCDC 태양광 발전 모드 처리"""
        self.logger.info("DCDC 태양광 발전 모드 실행")
        
        if self.dcdc_handler:
            # DCDC Solar Command (107) - 충전모드
            success = await self.dcdc_handler.write_register('solar_command', 85)
            if success:
                self.logger.info("DCDC 태양광 발전 모드 명령 전송 완료")
                await self.state_machine.trigger_event('dcdc_ready')
            else:
                self.logger.error("DCDC 태양광 발전 모드 명령 전송 실패")
                await self.state_machine.trigger_event('error')
        else:
            self.logger.warning("DCDC 핸들러가 없습니다. 정상 운전으로 전환합니다.")
            await self.state_machine.trigger_event('dcdc_ready')
    
    async def _handle_soc_high_wait(self):
        """SOC 상한 대기 처리"""
        self.logger.info("SOC 상한 도달 - DCDC 대기 모드로 전환")
        
        if self.dcdc_handler:
            # DCDC Ready Standby Command (106) - 대기모드
            success = await self.dcdc_handler.write_register('ready_standby_command', 85)
            if success:
                self.logger.info("DCDC 대기 모드 명령 전송 완료")
                # 설정된 대기 시간 후 자동으로 정상 운전으로 복귀 (state_machine에서 처리)
            else:
                self.logger.error("DCDC 대기 모드 명령 전송 실패")
    
    async def _handle_soc_low_charging(self):
        """SOC 하한 충전 처리"""
        self.logger.info("SOC 하한 도달 - 충전 시퀀스 시작")
        
        if self.pcs_handler:
            try:
                # 1. PCS Stop (20)
                await self.pcs_handler.write_register('pcs_stop', 85)
                self.logger.info("PCS 정지 명령 전송")
                await asyncio.sleep(5)
                
                # 2. PCS Standby Start (21) - PCS RUN
                await self.pcs_handler.write_register('pcs_standby_start', 85)
                self.logger.info("PCS 대기 시작 명령 전송")
                await asyncio.sleep(5)
                
                # 3. PCS Charge Start (22) - BAT 충전
                await self.pcs_handler.write_register('pcs_charge_start', 85)
                self.logger.info("PCS 충전 시작 명령 전송")
                
                # 4. 충전 전력 설정 (battery_charge_power 레지스터에 전력값 설정)
                charging_power = self.state_machine.charging_power
                charge_power_scaled = int(charging_power * 10)  # 0.1 scale
                await self.pcs_handler.write_register('battery_charge_power', charge_power_scaled)
                self.logger.info(f"충전 전력 설정: {charging_power} kW")
                
                # 충전 완료 모니터링은 SOC 모니터링에서 처리
                await self._start_charge_monitoring()
                
            except Exception as e:
                self.logger.error(f"충전 시퀀스 실행 중 오류: {e}")
                await self.state_machine.trigger_event('error')
    
    async def _start_charge_monitoring(self):
        """충전 완료 모니터링"""
        charge_stop_threshold = self.state_machine.soc_charge_stop_threshold
        
        while self.state_machine.get_current_state() == AutoModeState.SOC_LOW_CHARGING:
            try:
                if self.last_soc_value >= charge_stop_threshold:
                    self.logger.info(f"SOC {charge_stop_threshold}% 도달 - 충전 완료")
                    
                    # PCS Stop -> 독립운전 모드로 전환
                    await self.pcs_handler.write_register('pcs_stop', 85)
                    await asyncio.sleep(5)
                    await self.pcs_handler.write_register('inv_start_mode', 85)
                    
                    await self.state_machine.trigger_event('charge_complete')
                    break
                
                await asyncio.sleep(2)  # 2초마다 확인
                
            except Exception as e:
                self.logger.error(f"충전 모니터링 중 오류: {e}")
                break
    
    async def _handle_normal_operation(self):
        """정상 운전 처리"""
        self.logger.info("정상 운전 모드")
        # 정상 운전 상태에서는 SOC 모니터링만 계속하고 특별한 제어는 하지 않음
    
    async def _handle_stopping(self):
        """정지 처리"""
        self.logger.info("자동 운전 모드 정지 중...")
        
        # 모든 제어를 수동 상태로 복귀
        try:
            if self.pcs_handler:
                # 수동 독립운전 모드로 설정
                await self.pcs_handler.write_register('inv_start_mode', 85)
            
            if self.dcdc_handler:
                # DCDC 정상 운전 모드로 설정
                await self.dcdc_handler.write_register('solar_command', 85)
            
            self.logger.info("수동 운전 상태로 복귀 완료")
            await self.state_machine.trigger_event('stop_complete')
            
        except Exception as e:
            self.logger.error(f"정지 처리 중 오류: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """자동 운전 모드 상태 정보"""
        return {
            'auto_mode': self.state_machine.get_status(),
            'last_soc': self.last_soc_value,
            'devices': {
                'pcs_available': self.pcs_handler is not None,
                'dcdc_available': self.dcdc_handler is not None,
                'bms_available': self.bms_handler is not None
            }
        }
    
    def is_auto_mode_active(self) -> bool:
        """자동 모드 활성 상태 확인"""
        return self.state_machine.is_auto_mode_active() 