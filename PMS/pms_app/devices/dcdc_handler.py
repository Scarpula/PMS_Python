"""
DCDC 컨버터 핸들러
DCDC 장비에 특화된 데이터 읽기 및 처리 로직
"""

import asyncio
from typing import Dict, Any, Optional
from pymodbus.client.tcp import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import DeviceInterface


class DCDCHandler(DeviceInterface):
    """DCDC 컨버터 핸들러 클래스"""
    
    def __init__(self, device_config: Dict[str, Any], mqtt_client, system_config: Dict[str, Any]):
        """DCDC 핸들러 초기화"""
        super().__init__(device_config, mqtt_client, system_config)
        # 단일 Modbus 클라이언트와 비동기 락 사용
        self.modbus_client: Optional[AsyncModbusTcpClient] = None
        self._connection_lock = asyncio.Lock()
    
    async def _connect_modbus(self) -> bool:
        """Modbus TCP 연결 - 단일 클라이언트 및 락 사용"""
        async with self._connection_lock:
            try:
                # 클라이언트가 없거나 연결이 끊어졌으면 새로 생성
                if self.modbus_client is None or not self.modbus_client.connected:
                    self.modbus_client = AsyncModbusTcpClient(
                        host=self.ip,
                        port=self.port,
                        timeout=self.connection_timeout
                    )
                    self.logger.debug(f"새 DCDC Modbus 클라이언트 생성: {self.ip}:{self.port}")
                
                # 연결 시도 (이미 연결된 경우 다시 시도하지 않음)
                if not self.modbus_client.connected:
                    self.logger.debug(f"DCDC Modbus 연결 시도: {self.ip}:{self.port}")
                    self.connected = await self.modbus_client.connect()
                else:
                    self.connected = True # 이미 연결되어 있음
                
                if self.connected:
                    self.logger.debug(f"✅ DCDC Modbus 연결 성공: {self.ip}:{self.port}")
                else:
                    self.logger.warning(f"❌ DCDC Modbus 연결 실패: {self.ip}:{self.port}")
                    self.modbus_client = None # 실패 시 클라이언트 정리
                    
                return self.connected
                
            except Exception as e:
                self.logger.error(f"❌ DCDC Modbus 연결 중 오류: {e}")
                self.connected = False
                self.modbus_client = None # 오류 발생 시 클라이언트 정리
                return False

    async def _disconnect_modbus(self):
        """Modbus TCP 연결 해제"""
        if self.modbus_client and self.modbus_client.connected:
            self.modbus_client.close()
        self.connected = False
        self.modbus_client = None
        self.logger.debug("Modbus 연결 해제됨")

    async def read_data(self) -> Optional[Dict[str, Any]]:
        """
        DCDC 장비에서 데이터를 읽어옵니다.
        
        Returns:
            읽어온 원시 데이터 딕셔너리 또는 None (실패 시)
        """
        if not await self._ensure_connection():
            return None

        async with self._connection_lock:
            try:
                if not self.modbus_client or not self.modbus_client.connected:
                    self.logger.warning("데이터 읽기 시도 전 연결이 끊어졌습니다.")
                    return None
                
                raw_data = {}
                
                # 모든 레지스터 섹션을 읽기
                for section_name in ['parameter_registers', 'metering_registers', 'optional_metering_registers']:
                    section_registers = self.device_map.get(section_name, {})
                    
                    for key, register_info in section_registers.items():
                        try:
                            address = register_info['address']
                            data_type = register_info.get('data_type', 'uint16')
                            register_count = register_info.get('registers', 1)
                            function_code = register_info.get('function_code', '0x03')
                            
                            # Modbus 클라이언트 확인
                            if self.modbus_client is None:
                                self.logger.warning("Modbus 클라이언트가 None입니다")
                                continue
                            
                            # Function Code에 따른 읽기
                            if function_code == '0x03':
                                # Read Holding Registers
                                response = await self.modbus_client.read_holding_registers(
                                    address=address,
                                    count=register_count,
                                    slave=self.slave_id
                                )
                            elif function_code == '0x04':
                                # Read Input Registers
                                response = await self.modbus_client.read_input_registers(
                                    address=address,
                                    count=register_count,
                                    slave=self.slave_id
                                )
                            else:
                                self.logger.warning(f"지원하지 않는 Function Code: {function_code}")
                                continue
                            
                            if response.isError():
                                self.logger.warning(f"레지스터 읽기 실패 - {key}: {response}")
                                continue
                            
                            # 데이터 타입에 따른 값 변환
                            if register_count == 1:
                                raw_value = response.registers[0]
                                if data_type == 'int16' and raw_value > 32767:
                                    raw_value = raw_value - 65536
                            else:
                                # 32비트 데이터 (2개 레지스터)
                                if len(response.registers) >= 2:
                                    raw_value = (response.registers[0] << 16) + response.registers[1]
                                    if data_type == 'int32' and raw_value > 2147483647:
                                        raw_value = raw_value - 4294967296
                                else:
                                    raw_value = response.registers[0]
                            
                            raw_data[key] = raw_value
                            
                        except Exception as e:
                            self.logger.warning(f"레지스터 읽기 오류 - {key}: {e}")
                            continue
                
                if raw_data:
                    self.logger.debug(f"DCDC 데이터 읽기 완료: {len(raw_data)}개 레지스터")
                    return raw_data
                else:
                    self.logger.warning("읽어온 데이터가 없습니다")
                    return None
                
            except ModbusException as e:
                self.logger.error(f"Modbus 예외 발생: {e}")
                await self._disconnect_modbus()
                return None
            except Exception as e:
                self.logger.error(f"데이터 읽기 중 예외 발생: {e}")
                return None
    
    async def process_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        DCDC 원시 데이터를 가공합니다.
        
        Args:
            raw_data: 원시 데이터 딕셔너리
            
        Returns:
            가공된 데이터 딕셔너리
        """
        processed_data = {}
        
        # 모든 레지스터 섹션을 확인
        all_registers = {}
        for section in ['parameter_registers', 'metering_registers', 'control_registers', 'optional_metering_registers']:
            if section in self.device_map:
                all_registers.update(self.device_map[section])
        
        try:
            for key, raw_value in raw_data.items():
                if key in all_registers:
                    register_info = all_registers[key]
                    scale = register_info.get('scale', 1)
                    unit = register_info.get('unit', '')
                    description = register_info.get('description', key)
                    data_type = register_info.get('type', 'value')
                    
                    if data_type == 'bitmask':
                        # 비트마스크 처리
                        processed_data[key] = self._process_bitmask(raw_value, register_info, description)
                    else:
                        # 일반 값 처리
                        processed_value = raw_value * scale
                        processed_data[key] = {
                            'value': processed_value,
                            'unit': unit,
                            'description': description,
                            'raw_value': raw_value
                        }
                else:
                    # 맵에 없는 데이터는 원시값 그대로
                    processed_data[key] = {
                        'value': raw_value,
                        'unit': '',
                        'description': key,
                        'raw_value': raw_value
                    }
            
            # DCDC 특화 계산 (예: 효율 계산, 전력 계산 등)
            self._calculate_derived_values(processed_data)
            
            self.logger.debug(f"DCDC 데이터 가공 완료: {len(processed_data)}개 항목")
            return processed_data
            
        except Exception as e:
            self.logger.error(f"데이터 가공 중 오류: {e}")
            return {}
    
    def _process_bitmask(self, raw_value: int, register_info: Dict[str, Any], description: str) -> Dict[str, Any]:
        """
        비트마스크 데이터를 처리합니다.
        
        Args:
            raw_value: 원시 비트마스크 값
            register_info: 레지스터 정보
            description: 레지스터 설명
            
        Returns:
            처리된 비트마스크 데이터
        """
        bit_definitions = register_info.get('bit_definitions', {})
        active_bits = []
        bit_status = {}
        status_values = {}
        
        for bit_pos, bit_desc in bit_definitions.items():
            bit_num = int(bit_pos)
            is_set = bool(raw_value & (1 << bit_num))
            bit_status[f"bit_{bit_num:02d}"] = {
                'active': is_set,
                'description': bit_desc
            }
            
            # 비트 값에 따른 상태 해석
            status_value = self._interpret_bit_status(bit_num, is_set, bit_desc, raw_value)
            if status_value:
                status_values[f"bit_{bit_num:02d}_status"] = status_value
            
            if is_set:
                active_bits.append(f"Bit {bit_num}: {bit_desc}")
        
        # 특별한 레지스터에 대한 추가 처리
        additional_status = self._process_special_registers(register_info, raw_value, bit_status)
        
        return {
            'value': raw_value,
            'unit': '',
            'description': description,
            'raw_value': raw_value,
            'type': 'bitmask',
            'active_bits': active_bits,
            'bit_status': bit_status,
            'status_values': status_values,
            'additional_status': additional_status,
            'total_active': len(active_bits)
        }
    
    def _interpret_bit_status(self, bit_num: int, is_set: bool, bit_desc: str, raw_value: int) -> Optional[Dict[str, Any]]:
        """
        비트 상태를 해석하여 구체적인 값을 반환합니다.
        
        Args:
            bit_num: 비트 번호
            is_set: 비트가 설정되었는지 여부
            bit_desc: 비트 설명
            raw_value: 원시 값
            
        Returns:
            해석된 상태 정보 또는 None
        """
        # DCDC 운전 상태 특별 처리
        if "운전 상태" in bit_desc or "운전" in bit_desc:
            if "정지" in bit_desc:
                return {
                    'status': '정지' if is_set else '운전',
                    'code': 1 if is_set else 0,
                    'description': 'DCDC 정지 상태'
                }
            elif "대기" in bit_desc:
                return {
                    'status': '대기운전' if is_set else '운전',
                    'code': 1 if is_set else 0,
                    'description': 'DCDC 대기운전 상태'
                }
            elif "충전" in bit_desc:
                return {
                    'status': '충전운전' if is_set else '비충전',
                    'code': 1 if is_set else 0,
                    'description': 'DCDC 충전운전 상태'
                }
            elif "방전" in bit_desc:
                return {
                    'status': '방전운전' if is_set else '비방전',
                    'code': 1 if is_set else 0,
                    'description': 'DCDC 방전운전 상태'
                }
            elif "독립" in bit_desc:
                return {
                    'status': '독립운전' if is_set else '비독립운전',
                    'code': 1 if is_set else 0,
                    'description': 'DCDC 독립운전 상태'
                }
        
        # 정상 상태 특별 처리
        elif "정상 상태" in bit_desc:
            return {
                'status': '정상 상태' if is_set else '비정상 상태',
                'code': 1 if is_set else 0,
                'description': 'DCDC 정상 상태'
            }
        
        # 일반적인 비트 상태 처리 - 대괄호 안의 설명 파싱
        elif "[" in bit_desc and "]" in bit_desc:
            try:
                # 대괄호 안의 내용 추출
                start = bit_desc.find('[')
                end = bit_desc.find(']')
                if start != -1 and end != -1:
                    status_text = bit_desc[start+1:end]
                    parts = status_text.split('/')
                    
                    if len(parts) == 2:
                        # "0: Normal" 형태 파싱
                        false_part = parts[0].strip()
                        true_part = parts[1].strip()
                        
                        false_value = false_part.split(':', 1)[1].strip() if ':' in false_part else false_part
                        true_value = true_part.split(':', 1)[1].strip() if ':' in true_part else true_part
                        
                        return {
                            'status': true_value if is_set else false_value,
                            'code': 1 if is_set else 0,
                            'description': bit_desc.split('[')[0].strip()
                        }
            except:
                pass
        
        # 알람/에러 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['alarm', 'error', 'fault', 'warning']):
            return {
                'status': '경고/에러' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 온도 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['temperature', 'temp', '온도']):
            return {
                'status': '온도 이상' if is_set else '온도 정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 전압 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['voltage', 'volt', '전압']):
            return {
                'status': '전압 이상' if is_set else '전압 정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 전류 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['current', '전류']):
            return {
                'status': '전류 이상' if is_set else '전류 정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 기본 처리 - Reserved나 기타
        if "Reserved" in bit_desc or "reserved" in bit_desc.lower() or "RESERVED" in bit_desc:
            return {
                'status': '예약됨',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 최종 기본값
        return {
            'status': '활성' if is_set else '비활성',
            'code': 1 if is_set else 0,
            'description': bit_desc
        }
    
    def _process_special_registers(self, register_info: Dict[str, Any], raw_value: int, bit_status: Dict[str, Any]) -> Dict[str, Any]:
        """
        특별한 레지스터에 대한 추가 처리를 수행합니다.
        
        Args:
            register_info: 레지스터 정보
            raw_value: 원시 값
            bit_status: 비트 상태 정보
            
        Returns:
            추가 상태 정보
        """
        additional_status = {}
        
        # DCDC STATUS 2 레지스터 특별 처리
        if "STATUS 2" in register_info.get('description', ''):
            # 비트 0: CNV 정지 상태
            if bit_status.get('bit_00', {}).get('active', False):
                additional_status['converter_status'] = {
                    'code': 0,
                    'text': '정지',
                    'description': 'CNV 정지 상태'
                }
            # 비트 1: 대기운전 상태
            elif bit_status.get('bit_01', {}).get('active', False):
                additional_status['converter_status'] = {
                    'code': 1,
                    'text': '대기운전',
                    'description': '대기운전 상태'
                }
            # 비트 2: 충전운전 상태
            elif bit_status.get('bit_02', {}).get('active', False):
                additional_status['converter_status'] = {
                    'code': 2,
                    'text': '충전운전',
                    'description': '충전운전 상태'
                }
            # 비트 3: 방전운전 상태
            elif bit_status.get('bit_03', {}).get('active', False):
                additional_status['converter_status'] = {
                    'code': 3,
                    'text': '방전운전',
                    'description': '방전운전 상태'
                }
            # 비트 4: 독립운전 상태
            elif bit_status.get('bit_04', {}).get('active', False):
                additional_status['converter_status'] = {
                    'code': 4,
                    'text': '독립운전',
                    'description': '독립운전 상태'
                }
            else:
                additional_status['converter_status'] = {
                    'code': -1,
                    'text': '알 수 없음',
                    'description': '컨버터 상태'
                }
            
            # 비트 7: 정상 상태
            if bit_status.get('bit_07', {}).get('active', False):
                additional_status['system_status'] = {
                    'code': 1,
                    'text': '정상 상태',
                    'description': '정상 상태'
                }
            else:
                additional_status['system_status'] = {
                    'code': 0,
                    'text': '비정상 상태',
                    'description': '정상 상태'
                }
        
        return additional_status
    
    def _calculate_derived_values(self, processed_data: Dict[str, Any]):
        """
        DCDC 특화 계산값들을 추가합니다.
        
        Args:
            processed_data: 가공된 데이터 딕셔너리 (수정됨)
        """
        try:
            # 입력 전력 계산 (입력 전압 * 입력 전류)
            if 'input_voltage' in processed_data and 'input_current' in processed_data:
                input_power = (
                    processed_data['input_voltage']['value'] * 
                    processed_data['input_current']['value']
                )
                processed_data['calculated_input_power'] = {
                    'value': round(input_power, 2),
                    'unit': 'W',
                    'description': '계산된 입력 전력',
                    'raw_value': input_power
                }
            
            # 출력 전력 계산 (출력 전압 * 출력 전류)
            if 'output_voltage' in processed_data and 'output_current' in processed_data:
                output_power = (
                    processed_data['output_voltage']['value'] * 
                    processed_data['output_current']['value']
                )
                processed_data['calculated_output_power'] = {
                    'value': round(output_power, 2),
                    'unit': 'W',
                    'description': '계산된 출력 전력',
                    'raw_value': output_power
                }
            
            # 효율 계산
            if ('calculated_input_power' in processed_data and 
                'calculated_output_power' in processed_data and
                processed_data['calculated_input_power']['value'] > 0):
                
                efficiency = (
                    processed_data['calculated_output_power']['value'] / 
                    processed_data['calculated_input_power']['value'] * 100
                )
                processed_data['calculated_efficiency'] = {
                    'value': round(efficiency, 2),
                    'unit': '%',
                    'description': '계산된 효율',
                    'raw_value': efficiency
                }
                
        except Exception as e:
            self.logger.warning(f"파생값 계산 중 오류: {e}")
    
    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self._connect_modbus()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self._disconnect_modbus()

    async def write_register(self, register_name: str, value: int) -> bool:
        """
        지정된 레지스터에 값을 씁니다.
        
        Args:
            register_name: 쓰기를 원하는 레지스터의 이름 (맵 파일 기준)
            value: 쓸 값
            
        Returns:
            성공 여부 (True/False)
        """
        if not await self._ensure_connection():
            return False

        async with self._connection_lock:
            try:
                if not self.modbus_client or not self.modbus_client.connected:
                    self.logger.warning("데이터 쓰기 시도 전 연결이 끊어졌습니다.")
                    return False
                    
                all_registers = {
                    **self.device_map.get('parameter_registers', {}),
                    **self.device_map.get('control_registers', {})
                }
                
                if register_name not in all_registers:
                    self.logger.error(f"알 수 없는 레지스터 이름: {register_name}")
                    return False
                
                register_info = all_registers[register_name]
                address = register_info['address']
                
                self.logger.info(f"레지스터 쓰기: '{register_name}' (주소: {address}) -> {value}")
                
                response = await self.modbus_client.write_register(address, value, slave=self.slave_id)
                
                if response.isError():
                    self.logger.error(f"레지스터 쓰기 실패 - {register_name}: {response}")
                    return False
                
                self.logger.info(f"✅ 레지스터 쓰기 성공: {register_name}")
                return True
                
            except ModbusException as e:
                self.logger.error(f"Modbus 예외 발생 (쓰기) - {register_name}: {e}")
                await self._disconnect_modbus()
                return False
            except Exception as e:
                self.logger.error(f"레지스터 쓰기 중 오류 - {register_name}: {e}")
                return False

    async def _ensure_connection(self) -> bool:
        """연결을 확인하고, 끊겨있으면 재연결을 시도하는 헬퍼 함수"""
        if self.modbus_client and self.modbus_client.connected:
            return True
        self.logger.debug("연결이 끊겨있어 재연결을 시도합니다.")
        return await self._connect_modbus()
    
    async def set_operation_mode(self, mode: str) -> bool:
        """
        DCDC 운전 모드 설정
        
        Args:
            mode: 'stop'(정지), 'standby'(대기), 'charge'(충전), 'discharge'(방전), 'independent'(독립) 중 하나
            
        Returns:
            성공 여부
        """
        mode_values = {
            'stop': 0,        # 정지
            'standby': 1,     # 대기운전
            'charge': 2,      # 충전운전
            'discharge': 3,   # 방전운전
            'independent': 4  # 독립운전
        }
        
        if mode not in mode_values:
            self.logger.error(f"지원하지 않는 운전 모드: {mode}")
            return False
        
        return await self.write_register('operation_mode_control', mode_values[mode])
    
    async def set_current_reference(self, current_a: float) -> bool:
        """
        DCDC 출력 전류 설정점 설정
        
        Args:
            current_a: 설정할 전류값 (A)
            
        Returns:
            성공 여부
        """
        # 스케일 팩터 적용
        control_registers = self.device_map.get('control_registers', {})
        if 'current_reference' in control_registers:
            scale = control_registers['current_reference'].get('scale', 1)
            value = int(current_a / scale)
            return await self.write_register('current_reference', value)
        else:
            self.logger.error("current_reference 레지스터를 찾을 수 없습니다")
            return False
    
    async def set_voltage_reference(self, voltage_v: float) -> bool:
        """
        DCDC 출력 전압 설정점 설정
        
        Args:
            voltage_v: 설정할 전압값 (V)
            
        Returns:
            성공 여부
        """
        # 스케일 팩터 적용
        control_registers = self.device_map.get('control_registers', {})
        if 'voltage_reference' in control_registers:
            scale = control_registers['voltage_reference'].get('scale', 1)
            value = int(voltage_v / scale)
            return await self.write_register('voltage_reference', value)
        else:
            self.logger.error("voltage_reference 레지스터를 찾을 수 없습니다")
            return False
    
    async def reset_faults(self) -> bool:
        """
        DCDC 고장 리셋
        
        Returns:
            성공 여부
        """
        return await self.write_register('fault_reset', 1)
    
    async def handle_control_message(self, payload: Dict[str, Any]):
        """
        MQTT 제어 메시지를 처리합니다.
        지원 명령:
          - operation_mode : { "command": "operation_mode", "mode": "stop/standby/charge/discharge/independent" }
          - current_reference : { "command": "current_reference", "current_a": 10.5 }
          - voltage_reference : { "command": "voltage_reference", "voltage_v": 380.0 }
          - reset_faults : { "command": "reset_faults" }
        """
        try:
            command = payload.get("command")
            
            if command == "operation_mode":
                mode = payload.get("mode")
                if mode:
                    result = await self.set_operation_mode(mode)
                    self.logger.info(f"DCDC 운전 모드 설정 {'성공' if result else '실패'}: {mode}")
                else:
                    self.logger.warning("운전 모드가 지정되지 않았습니다")
            
            elif command == "current_reference":
                current_a = payload.get("current_a")
                if current_a is not None:
                    result = await self.set_current_reference(float(current_a))
                    self.logger.info(f"DCDC 출력 전류 설정 {'성공' if result else '실패'}: {current_a}A")
                else:
                    self.logger.warning("출력 전류값이 지정되지 않았습니다")
            
            elif command == "voltage_reference":
                voltage_v = payload.get("voltage_v")
                if voltage_v is not None:
                    result = await self.set_voltage_reference(float(voltage_v))
                    self.logger.info(f"DCDC 출력 전압 설정 {'성공' if result else '실패'}: {voltage_v}V")
                else:
                    self.logger.warning("출력 전압값이 지정되지 않았습니다")
            
            elif command == "reset_faults":
                result = await self.reset_faults()
                self.logger.info(f"DCDC 고장 리셋 {'성공' if result else '실패'}")
            
            else:
                self.logger.warning(f"알 수 없는 DCDC 제어 명령: {payload}")
                
        except Exception as e:
            self.logger.error(f"DCDC 제어 메시지 처리 중 오류: {e}") 