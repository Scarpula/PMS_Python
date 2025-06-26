"""
자동 운전 모드 상태 머신
PMS 자동 운전 모드의 상태 전환 및 시퀀스를 관리합니다.
"""

import asyncio
import logging
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta


class AutoModeState(Enum):
    """자동 운전 모드 상태"""
    IDLE = "idle"                           # 대기 상태
    INITIALIZING = "initializing"           # 초기화 중
    PCS_STANDBY = "pcs_standby"            # PCS 대기 모드
    PCS_INVERTER = "pcs_inverter"          # PCS 독립 운전 모드
    DCDC_RESET = "dcdc_reset"              # DCDC 리셋
    DCDC_SOLAR = "dcdc_solar"              # DCDC 태양광 발전
    SOC_HIGH_WAIT = "soc_high_wait"        # SOC 상한 대기
    SOC_LOW_CHARGING = "soc_low_charging"   # SOC 하한 충전
    NORMAL_OPERATION = "normal_operation"   # 정상 운전
    ERROR = "error"                        # 오류 상태
    STOPPING = "stopping"                  # 정지 중


@dataclass
class StateTransition:
    """상태 전환 정보"""
    from_state: Optional[AutoModeState]
    to_state: AutoModeState
    trigger: str
    condition: Optional[str] = None
    delay_seconds: int = 0


class AutoModeStateMachine:
    """자동 운전 모드 상태 머신"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        상태 머신 초기화
        
        Args:
            config: 자동 운전 모드 설정
        """
        self.config = config.get('auto_mode', {})
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 상태 관리
        self.current_state = AutoModeState.IDLE
        self.previous_state = AutoModeState.IDLE
        self.state_start_time = datetime.now()
        
        # SOC 임계값 (동적 업데이트 가능)
        self.soc_high_threshold = self.config.get('soc_high_threshold', 88.0)
        self.soc_low_threshold = self.config.get('soc_low_threshold', 5.0)
        self.soc_charge_stop_threshold = self.config.get('soc_charge_stop_threshold', 25.0)
        
        # 대기 시간 설정 (동적 업데이트 가능)
        self.dcdc_standby_time = self.config.get('dcdc_standby_time', 30)  # 초
        self.command_interval = self.config.get('command_interval', 5)     # 초
        self.charging_power = self.config.get('charging_power', 10.0)      # kW
        
        # 상태 전환 규칙
        self.transitions = self._define_transitions()
        
        # 상태 변경 콜백
        self.state_change_callbacks = []
        
        # 타이머 관리
        self.pending_transition = None
        self.transition_timer_task = None
        
        self.logger.info("자동 운전 모드 상태 머신 초기화 완료")
    
    def _define_transitions(self) -> Dict[str, StateTransition]:
        """상태 전환 규칙 정의"""
        transitions = {}
        
        # 기본 시퀀스 전환
        transitions['start_auto'] = StateTransition(
            AutoModeState.IDLE, AutoModeState.INITIALIZING, 'start_auto'
        )
        transitions['init_to_pcs_standby'] = StateTransition(
            AutoModeState.INITIALIZING, AutoModeState.PCS_STANDBY, 'init_complete'
        )
        transitions['pcs_standby_to_inverter'] = StateTransition(
            AutoModeState.PCS_STANDBY, AutoModeState.PCS_INVERTER, 'timer',
            delay_seconds=self.command_interval
        )
        transitions['inverter_to_dcdc_reset'] = StateTransition(
            AutoModeState.PCS_INVERTER, AutoModeState.DCDC_RESET, 'pcs_ready'
        )
        transitions['dcdc_reset_to_solar'] = StateTransition(
            AutoModeState.DCDC_RESET, AutoModeState.DCDC_SOLAR, 'timer',
            delay_seconds=self.command_interval
        )
        transitions['solar_to_normal'] = StateTransition(
            AutoModeState.DCDC_SOLAR, AutoModeState.NORMAL_OPERATION, 'dcdc_ready'
        )
        
        # SOC 기반 전환
        transitions['normal_to_soc_high'] = StateTransition(
            AutoModeState.NORMAL_OPERATION, AutoModeState.SOC_HIGH_WAIT, 'soc_high'
        )
        transitions['soc_high_to_normal'] = StateTransition(
            AutoModeState.SOC_HIGH_WAIT, AutoModeState.NORMAL_OPERATION, 'timer',
            delay_seconds=self.dcdc_standby_time
        )
        transitions['normal_to_soc_low'] = StateTransition(
            AutoModeState.NORMAL_OPERATION, AutoModeState.SOC_LOW_CHARGING, 'soc_low'
        )
        transitions['soc_low_to_normal'] = StateTransition(
            AutoModeState.SOC_LOW_CHARGING, AutoModeState.NORMAL_OPERATION, 'charge_complete'
        )
        
        # 정지 전환
        transitions['stop_auto'] = StateTransition(
            None, AutoModeState.STOPPING, 'stop_auto'  # Any state can transition to stopping
        )
        transitions['stopping_to_idle'] = StateTransition(
            AutoModeState.STOPPING, AutoModeState.IDLE, 'stop_complete'
        )
        
        # 오류 전환
        transitions['to_error'] = StateTransition(
            None, AutoModeState.ERROR, 'error'  # Any state can transition to error
        )
        transitions['error_to_idle'] = StateTransition(
            AutoModeState.ERROR, AutoModeState.IDLE, 'reset_error'
        )
        
        return transitions
    
    async def start_auto_mode(self):
        """자동 운전 모드 시작"""
        self.logger.info(f"🚀 자동 모드 시작 요청 - 현재 상태: {self.current_state.value}")
        
        if self.current_state != AutoModeState.IDLE:
            self.logger.warning(f"❌ 자동 모드를 시작할 수 없는 상태입니다: {self.current_state.value}")
            self.logger.info(f"💡 자동 모드 시작 조건: 현재 상태가 IDLE이어야 함")
            
            # ERROR 상태인 경우 강제 리셋
            if self.current_state == AutoModeState.ERROR:
                self.logger.info("🔄 ERROR 상태 감지 - 강제 리셋 후 재시작")
                await self._transition_to(AutoModeState.IDLE, 'force_reset')
                await asyncio.sleep(0.1)  # 잠시 대기
                self.logger.info("🚀 리셋 완료 - 자동 모드 재시작")
                await self._transition_to(AutoModeState.INITIALIZING, 'start_auto')
                return True
            # 다른 상태(STOPPING 등)인 경우도 IDLE로 리셋
            elif self.current_state in [AutoModeState.STOPPING]:
                self.logger.info(f"🔄 {self.current_state.value} 상태에서 IDLE로 리셋")
                await self._transition_to(AutoModeState.IDLE, 'force_reset')
                await asyncio.sleep(0.1)  # 잠시 대기
                await self._transition_to(AutoModeState.INITIALIZING, 'start_auto')
                return True
            else:
                return False
        
        self.logger.info("✅ 자동 모드 시작 조건 만족 - INITIALIZING 상태로 전환")
        await self._transition_to(AutoModeState.INITIALIZING, 'start_auto')
        return True
    
    async def stop_auto_mode(self):
        """자동 운전 모드 정지"""
        if self.current_state == AutoModeState.IDLE:
            self.logger.info("자동 모드가 이미 대기 상태입니다")
            return True
        
        await self._transition_to(AutoModeState.STOPPING, 'stop_auto')
        return True
    
    async def trigger_event(self, event: str, data: Optional[Dict[str, Any]] = None):
        """
        이벤트 트리거
        
        Args:
            event: 이벤트 이름
            data: 이벤트 데이터
        """
        self.logger.debug(f"이벤트 트리거: {event}, 현재 상태: {self.current_state.value}")
        
        # SOC 이벤트 처리
        if event == 'soc_update' and data:
            await self._handle_soc_update(data.get('soc', 0))
            return
        
        # 다른 이벤트 처리
        for transition_key, transition in self.transitions.items():
            if (transition.trigger == event and 
                (transition.from_state is None or transition.from_state == self.current_state)):
                
                if transition.delay_seconds > 0:
                    await self._schedule_transition(transition.to_state, transition.delay_seconds)
                else:
                    await self._transition_to(transition.to_state, event)
                break
    
    async def _handle_soc_update(self, soc_value: float):
        """SOC 업데이트 처리"""
        if self.current_state != AutoModeState.NORMAL_OPERATION:
            return
        
        if soc_value >= self.soc_high_threshold:
            await self._transition_to(AutoModeState.SOC_HIGH_WAIT, 'soc_high')
        elif soc_value <= self.soc_low_threshold:
            await self._transition_to(AutoModeState.SOC_LOW_CHARGING, 'soc_low')
    
    async def _transition_to(self, new_state: AutoModeState, trigger: str):
        """상태 전환 실행"""
        if self.current_state == new_state:
            return
        
        self.logger.info(f"상태 전환: {self.current_state.value} -> {new_state.value} (트리거: {trigger})")
        
        # 진행 중인 타이머 취소
        if self.transition_timer_task:
            self.transition_timer_task.cancel()
            self.transition_timer_task = None
        
        # 상태 변경
        self.previous_state = self.current_state
        self.current_state = new_state
        self.state_start_time = datetime.now()
        
        # 콜백 실행
        for callback in self.state_change_callbacks:
            try:
                await callback(self.previous_state, self.current_state, trigger)
            except Exception as e:
                self.logger.error(f"상태 변경 콜백 실행 중 오류: {e}")
    
    async def _schedule_transition(self, to_state: AutoModeState, delay_seconds: int):
        """지연된 상태 전환 예약"""
        if self.transition_timer_task:
            self.transition_timer_task.cancel()
        
        self.logger.debug(f"{delay_seconds}초 후 {to_state.value}로 전환 예약")
        
        async def delayed_transition():
            try:
                await asyncio.sleep(delay_seconds)
                await self._transition_to(to_state, 'timer')
            except asyncio.CancelledError:
                pass
        
        self.transition_timer_task = asyncio.create_task(delayed_transition())
    
    def add_state_change_callback(self, callback):
        """상태 변경 콜백 추가"""
        self.state_change_callbacks.append(callback)
    
    def get_current_state(self) -> AutoModeState:
        """현재 상태 반환"""
        return self.current_state
    
    def get_state_duration(self) -> timedelta:
        """현재 상태 지속 시간"""
        return datetime.now() - self.state_start_time
    
    def is_auto_mode_active(self) -> bool:
        """자동 모드가 활성 상태인지 확인"""
        return self.current_state not in [AutoModeState.IDLE, AutoModeState.ERROR, AutoModeState.STOPPING]

    def update_thresholds(self, threshold_config: Dict[str, Any]) -> (bool, str):
        """
        MQTT 메시지로부터 임계값을 동적으로 업데이트합니다.
        
        Args:
            threshold_config: 임계값 설정 딕셔너리
            
        Returns:
            (성공 여부, 결과 메시지) 튜플
        """
        self.logger.info(f"임계값 업데이트 시도: {threshold_config}")
        
        updated_params = []
        try:
            # 필수 키 확인
            required_keys = ['soc_high_threshold', 'soc_low_threshold', 'soc_charge_stop_threshold']
            if not all(key in threshold_config for key in required_keys):
                missing_keys = [key for key in required_keys if key not in threshold_config]
                message = f"필수 임계값 누락: {', '.join(missing_keys)}"
                self.logger.error(message)
                return False, message

            # SOC 상한/하한 임계값
            new_soc_high = float(threshold_config['soc_high_threshold'])
            new_soc_low = float(threshold_config['soc_low_threshold'])
            
            if new_soc_low >= new_soc_high:
                message = f"SOC 하한({new_soc_low}%)은 상한({new_soc_high}%)보다 작아야 합니다."
                self.logger.error(message)
                return False, message
            
            self.soc_high_threshold = new_soc_high
            self.soc_low_threshold = new_soc_low
            updated_params.extend([f"SOC 상한: {new_soc_high}%", f"SOC 하한: {new_soc_low}%"])

            # 충전 정지 임계값
            self.soc_charge_stop_threshold = float(threshold_config['soc_charge_stop_threshold'])
            updated_params.append(f"충전 정지: {self.soc_charge_stop_threshold}%")

            # 선택적 파라미터
            if 'dcdc_standby_time' in threshold_config:
                self.dcdc_standby_time = int(threshold_config['dcdc_standby_time'])
                updated_params.append(f"DCDC 대기: {self.dcdc_standby_time}초")
            
            if 'command_interval' in threshold_config:
                self.command_interval = int(threshold_config['command_interval'])
                updated_params.append(f"명령 간격: {self.command_interval}초")
            
            if 'charging_power' in threshold_config:
                self.charging_power = float(threshold_config['charging_power'])
                updated_params.append(f"충전 전력: {self.charging_power}kW")

            success_message = f"임계값 업데이트 성공: {', '.join(updated_params)}"
            self.logger.info(success_message)
            return True, success_message

        except (ValueError, TypeError) as e:
            error_message = f"임계값 파라미터 타입 오류: {e}"
            self.logger.error(error_message)
            return False, error_message
        except Exception as e:
            error_message = f"임계값 업데이트 중 알 수 없는 오류: {e}"
            self.logger.error(error_message, exc_info=True)
            return False, error_message

    def get_status(self) -> Dict[str, Any]:
        """상태 정보 반환"""
        return {
            'current_state': self.current_state.value,
            'previous_state': self.previous_state.value,
            'state_duration_seconds': self.get_state_duration().total_seconds(),
            'is_active': self.is_auto_mode_active(),
            'config': {
                'soc_high_threshold': self.soc_high_threshold,
                'soc_low_threshold': self.soc_low_threshold,
                'soc_charge_stop_threshold': self.soc_charge_stop_threshold,
                'dcdc_standby_time': self.dcdc_standby_time,
                'command_interval': self.command_interval,
                'charging_power': self.charging_power
            }
        } 