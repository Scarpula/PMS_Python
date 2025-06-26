"""
BMS (Battery Management System) 핸들러
범한배터리 BMS 장비에 특화된 데이터 읽기 및 처리 로직
Function Code 0x03: Read Holding Register
Function Code 0x06: Write Single Register
"""

import asyncio
from typing import Dict, Any, Optional
from pymodbus.client.tcp import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import DeviceInterface


class BMSHandler(DeviceInterface):
    """BMS 핸들러 클래스 - 범한배터리 BMS 전용"""
    
    def __init__(self, device_config: Dict[str, Any], mqtt_client, system_config: Dict[str, Any]):
        """BMS 핸들러 초기화"""
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
                    self.logger.debug(f"새 BMS Modbus 클라이언트 생성: {self.ip}:{self.port}")
                
                # 연결 시도 (이미 연결된 경우 다시 시도하지 않음)
                if not self.modbus_client.connected:
                    self.logger.debug(f"BMS Modbus 연결 시도: {self.ip}:{self.port}")
                    self.connected = await self.modbus_client.connect()
                else:
                    self.connected = True # 이미 연결되어 있음
                
                if self.connected:
                    self.logger.debug(f"✅ BMS Modbus 연결 성공: {self.ip}:{self.port}")
                else:
                    self.logger.warning(f"❌ BMS Modbus 연결 실패: {self.ip}:{self.port}")
                    self.modbus_client = None # 실패 시 클라이언트 정리
                    
                return self.connected
                
            except Exception as e:
                self.logger.error(f"❌ BMS Modbus 연결 중 오류: {e}")
                self.connected = False
                self.modbus_client = None # 오류 발생 시 클라이언트 정리
                return False
    
    async def _disconnect_modbus(self):
        """Modbus TCP 연결 해제"""
        if self.modbus_client and self.modbus_client.connected:
            self.modbus_client.close()
        self.connected = False
        self.modbus_client = None
        self.logger.debug("BMS Modbus 연결 해제됨")
    
    async def read_data(self) -> Optional[Dict[str, Any]]:
        """
        BMS 장비에서 데이터를 읽어옵니다.
        Function Code 0x03 (Read Holding Register) 사용
        
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
                
                # 각 섹션별로 데이터 읽기
                sections = [
                    'data_registers',
                    'module_voltages', 
                    'status_registers',
                    'module_status_registers',
                    'module_temperatures',
                    'cell_voltages',
                    'optional_metering_registers'
                ]
                
                for section in sections:
                    section_data = self.device_map.get(section, {})
                    for key, register_info in section_data.items():
                        try:
                            # Function Code가 0x03 (Read)인 것만 읽기
                            if register_info.get('function_code') != '0x03':
                                continue
                                
                            address = register_info['address']
                            data_type = register_info.get('data_type', 'uint16')
                            register_count = register_info.get('registers', 1)
                            
                            # Function Code 0x03: Read Holding Registers
                            if self.modbus_client is None:
                                self.logger.error("Modbus 클라이언트가 초기화되지 않았습니다")
                                continue
                                
                            response = await self.modbus_client.read_holding_registers(
                                address=address,
                                count=register_count,
                                slave=self.slave_id
                            )
                            
                            if response.isError():
                                self.logger.debug(f"레지스터 읽기 실패 - {key} (addr:{address}): {response}")
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
                            self.logger.debug(f"레지스터 읽기 오류 - {key}: {e}")
                            continue
                
                if raw_data:
                    self.logger.debug(f"BMS 데이터 읽기 완료: {len(raw_data)}개 레지스터")
                    return raw_data
                else:
                    self.logger.warning("BMS에서 읽어온 데이터가 없습니다")
                    return None
                
            except ModbusException as e:
                self.logger.error(f"BMS Modbus 예외 발생: {e}")
                await self._disconnect_modbus()
                return None
            except Exception as e:
                self.logger.error(f"BMS 데이터 읽기 중 예외 발생: {e}")
                return None
    
    async def write_register(self, register_name: str, value: int) -> bool:
        """
        BMS 제어 레지스터에 값을 씁니다.
        Function Code 0x06 (Write Single Register) 사용
        
        Args:
            register_name: 레지스터 이름
            value: 쓸 값
            
        Returns:
            성공 여부
        """
        if not await self._ensure_connection():
            return False

        async with self._connection_lock:
            try:
                if not self.modbus_client or not self.modbus_client.connected:
                    self.logger.warning("데이터 쓰기 시도 전 연결이 끊어졌습니다.")
                    return False

                control_registers = self.device_map.get('control_registers', {})
                
                if register_name not in control_registers:
                    self.logger.error(f"알 수 없는 제어 레지스터: {register_name}")
                    return False
                
                register_info = control_registers[register_name]
                
                # Function Code가 0x06 (Write)인지 확인
                if register_info.get('function_code') != '0x06':
                    self.logger.error(f"읽기 전용 레지스터입니다: {register_name}")
                    return False
                
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
                self.logger.error(f"BMS 레지스터 쓰기 중 오류 - {register_name}: {e}")
                return False
    
    async def process_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        BMS 원시 데이터를 가공합니다.
        
        Args:
            raw_data: 원시 데이터 딕셔너리
            
        Returns:
            가공된 데이터 딕셔너리
        """
        processed_data = {}
        
        # 모든 섹션에서 레지스터 정보 가져오기
        all_registers = {}
        sections = [
            'data_registers',
            'module_voltages', 
            'status_registers',
            'module_status_registers',
            'module_temperatures',
            'cell_voltages',
            'optional_metering_registers'
        ]
        
        for section in sections:
            all_registers.update(self.device_map.get(section, {}))
        
        try:
            for key, raw_value in raw_data.items():
                if key in all_registers:
                    register_info = all_registers[key]
                    scale = register_info.get('scale', 1)
                    unit = register_info.get('unit', '')
                    description = register_info.get('description', key)
                    register_type = register_info.get('type', '')
                    
                    # 스케일 적용
                    processed_value = raw_value * scale
                    
                    # 비트마스크 타입 처리
                    if register_type == 'bitmask':
                        processed_data[key] = self._process_bitmask(raw_value, register_info, description)
                    else:
                        processed_data[key] = {
                            'value': processed_value,
                            'unit': unit,
                            'description': description,
                            'raw_value': raw_value,
                            'type': register_type
                        }
                else:
                    # 맵에 없는 데이터는 원시값 그대로
                    processed_data[key] = {
                        'value': raw_value,
                        'unit': '',
                        'description': key,
                        'raw_value': raw_value,
                        'type': 'unknown'
                    }
            
            # BMS 특화 계산
            self._calculate_derived_values(processed_data)
            
            self.logger.debug(f"BMS 데이터 가공 완료: {len(processed_data)}개 항목")
            return processed_data
            
        except Exception as e:
            self.logger.error(f"BMS 데이터 가공 중 오류: {e}")
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
            'total_active': len(active_bits),
            'bit_flags': bin(raw_value)[2:].zfill(16)
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
        # Fire Alarm 특별 처리
        if "Fire Alarm" in bit_desc:
            return {
                'status': '화재 경보' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': '화재 경보 상태'
            }
        
        # Smoke Sensor 특별 처리
        elif "Smoke Sensor" in bit_desc:
            return {
                'status': '연기 감지' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': '연기 센서 상태'
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
        if "Reserved" in bit_desc or "reserved" in bit_desc.lower():
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
        
        # Fire Alarm 레지스터 특별 처리
        if "Fire Alarm" in register_info.get('description', ''):
            # 비트 0: Smoke Sensor Status
            if bit_status.get('bit_00', {}).get('active', False):
                additional_status['smoke_sensor'] = {
                    'code': 1,
                    'text': '고장',
                    'description': 'Smoke Sensor Status'
                }
            else:
                additional_status['smoke_sensor'] = {
                    'code': 0,
                    'text': '정상',
                    'description': 'Smoke Sensor Status'
                }
            
            # 비트 15: Fire Alarm
            if bit_status.get('bit_15', {}).get('active', False):
                additional_status['fire_alarm'] = {
                    'code': 1,
                    'text': '화재 경보',
                    'description': 'Fire Alarm'
                }
            else:
                additional_status['fire_alarm'] = {
                    'code': 0,
                    'text': '정상',
                    'description': 'Fire Alarm'
                }
        
        return additional_status

    def _calculate_derived_values(self, processed_data: Dict[str, Any]):
        """
        BMS 특화 계산값들을 추가합니다.
        
        Args:
            processed_data: 가공된 데이터 딕셔너리 (수정됨)
        """
        try:
            # 셀 전압 차이 계산
            if 'battery_cell_max_voltage' in processed_data and 'battery_cell_min_voltage' in processed_data:
                voltage_diff = (
                    processed_data['battery_cell_max_voltage']['value'] - 
                    processed_data['battery_cell_min_voltage']['value']
                )
                processed_data['cell_voltage_diff'] = {
                    'value': round(voltage_diff, 3),
                    'unit': 'V',
                    'description': '셀 전압 차이 (최대-최소)',
                    'raw_value': voltage_diff,
                    'type': 'calculated'
                }
            
            # 모듈 온도 차이 계산
            if 'module_max_temperature' in processed_data and 'module_min_temperature' in processed_data:
                temp_diff = (
                    processed_data['module_max_temperature']['value'] - 
                    processed_data['module_min_temperature']['value']
                )
                processed_data['module_temp_diff'] = {
                    'value': round(temp_diff, 1),
                    'unit': '°C',
                    'description': '모듈 온도 차이 (최대-최소)',
                    'raw_value': temp_diff,
                    'type': 'calculated'
                }
            
            # 순간 전력 계산 (전압 * 전류)
            if 'rack_voltage' in processed_data and 'rack_current' in processed_data:
                instantaneous_power = (
                    processed_data['rack_voltage']['value'] * 
                    processed_data['rack_current']['value']
                )
                processed_data['instantaneous_power'] = {
                    'value': round(instantaneous_power, 2),
                    'unit': 'W',
                    'description': '순간 전력 (랙 전압 × 랙 전류)',
                    'raw_value': instantaneous_power,
                    'type': 'calculated'
                }
            
            # SOC 상태 해석
            if 'battery_soc' in processed_data:
                soc_value = processed_data['battery_soc']['value']
                if soc_value >= 80:
                    soc_status = '높음'
                    soc_level = 'HIGH'
                elif soc_value >= 50:
                    soc_status = '보통'
                    soc_level = 'NORMAL'
                elif soc_value >= 20:
                    soc_status = '낮음'
                    soc_level = 'LOW'
                else:
                    soc_status = '매우 낮음'
                    soc_level = 'CRITICAL'
                
                processed_data['soc_status'] = {
                    'value': soc_status,
                    'unit': '',
                    'description': 'SOC 상태',
                    'raw_value': soc_value,
                    'type': 'status',
                    'level': soc_level
                }
            
            # 시스템 운영 모드 해석
            if 'battery_system_operation_mode' in processed_data:
                mode_value = processed_data['battery_system_operation_mode']['raw_value']
                mode_status = []
                
                if mode_value & 0x01:
                    mode_status.append('초기화 완료')
                else:
                    mode_status.append('초기화 중')
                    
                if mode_value & 0x02:
                    mode_status.append('충전 중')
                if mode_value & 0x04:
                    mode_status.append('방전 중')
                if mode_value & 0x08:
                    mode_status.append('대기 (릴레이 ON)')
                
                processed_data['system_mode_status'] = {
                    'value': ', '.join(mode_status) if mode_status else '알 수 없음',
                    'unit': '',
                    'description': '시스템 운영 모드',
                    'raw_value': mode_value,
                    'type': 'status'
                }
            
            # 알람 및 에러 상태 요약
            alarm_count = 0
            error_count = 0
            warning_count = 0
            
            for key, data in processed_data.items():
                if data.get('type') == 'bitmask':
                    active_bits = data.get('active_bits', [])
                    if 'alarm' in key.lower():
                        alarm_count += len(active_bits)
                    elif 'error' in key.lower():
                        error_count += len(active_bits)
                    elif 'warning' in key.lower():
                        warning_count += len(active_bits)
            
            processed_data['system_health_summary'] = {
                'value': f'알람: {alarm_count}, 에러: {error_count}, 경고: {warning_count}',
                'unit': '',
                'description': '시스템 건강 상태 요약',
                'raw_value': {'alarms': alarm_count, 'errors': error_count, 'warnings': warning_count},
                'type': 'summary'
            }
                
        except Exception as e:
            self.logger.warning(f"BMS 파생값 계산 중 오류: {e}")
    
    async def control_dc_contactor(self, enable: bool) -> bool:
        """
        DC 접촉기 제어
        
        Args:
            enable: True=ON, False=OFF
            
        Returns:
            성공 여부
        """
        value = 1 if enable else 0
        result = await self.write_register('dc_contactor_control', value)
        
        if result:
            status = "ON" if enable else "OFF"
            self.logger.info(f"BMS DC 접촉기 {status} 명령 전송됨")
        
        return result
    
    async def reset_errors(self) -> bool:
        """에러 리셋 명령"""
        return await self.write_register('error_reset', 0x0050)
    
    async def reset_system_lock(self) -> bool:
        """시스템 락 리셋 명령"""
        return await self.write_register('system_lock_reset', 0x0050)
    
    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self._connect_modbus()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self._disconnect_modbus()
    
    async def handle_control_message(self, payload: Dict[str, Any]):
        """
        MQTT 제어 메시지를 처리합니다.
        지원 명령:
          - dc_contactor : { "command": "dc_contactor", "enable": true/false }
          - reset_errors : { "command": "reset_errors" }
          - reset_system_lock : { "command": "reset_system_lock" }
        """
        try:
            command = payload.get("command")
            
            if command == "dc_contactor":
                enable = bool(payload.get("enable", True))
                result = await self.control_dc_contactor(enable)
                status = "ON" if enable else "OFF"
                self.logger.info(f"BMS DC 접촉기 {status} 명령 {'성공' if result else '실패'}")
                
            elif command == "reset_errors":
                result = await self.reset_errors()
                self.logger.info(f"BMS 에러 리셋 {'성공' if result else '실패'}")
                
            elif command == "reset_system_lock":
                result = await self.reset_system_lock()
                self.logger.info(f"BMS 시스템 락 리셋 {'성공' if result else '실패'}")
                
            else:
                self.logger.warning(f"알 수 없는 BMS 제어 명령: {payload}")
                
        except Exception as e:
            self.logger.error(f"BMS 제어 메시지 처리 중 오류: {e}") 

    async def _ensure_connection(self) -> bool:
        """연결을 확인하고, 끊겨있으면 재연결을 시도하는 헬퍼 함수"""
        if self.modbus_client and self.modbus_client.connected:
            return True
        self.logger.debug("연결이 끊겨있어 재연결을 시도합니다.")
        return await self._connect_modbus() 