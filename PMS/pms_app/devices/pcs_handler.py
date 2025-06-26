"""
PCS (Power Conversion System) 핸들러
PCS 장비에 특화된 데이터 읽기 및 처리 로직
"""

import asyncio
from typing import Dict, Any, Optional
from pymodbus.client.tcp import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import DeviceInterface


class PCSHandler(DeviceInterface):
    """PCS 핸들러 클래스"""
    
    def __init__(self, device_config: Dict[str, Any], mqtt_client, system_config: Dict[str, Any]):
        """PCS 핸들러 초기화"""
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
                    self.logger.debug(f"새 PCS Modbus 클라이언트 생성: {self.ip}:{self.port}")
                
                # 연결 시도 (이미 연결된 경우 다시 시도하지 않음)
                if not self.modbus_client.connected:
                    self.logger.debug(f"PCS Modbus 연결 시도: {self.ip}:{self.port}")
                    self.connected = await self.modbus_client.connect()
                else:
                    self.connected = True # 이미 연결되어 있음
                
                if self.connected:
                    self.logger.debug(f"✅ PCS Modbus 연결 성공: {self.ip}:{self.port}")
                else:
                    self.logger.warning(f"❌ PCS Modbus 연결 실패: {self.ip}:{self.port}")
                    self.modbus_client = None # 실패 시 클라이언트 정리
                    
                return self.connected
                
            except Exception as e:
                self.logger.error(f"❌ PCS Modbus 연결 중 오류: {e}")
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
        PCS 장비에서 데이터를 읽어옵니다.
        
        Returns:
            읽어온 원시 데이터 딕셔너리 또는 None (실패 시)
        """
        if not await self._ensure_connection():
            return None

        async with self._connection_lock:
            try:
                # Modbus 클라이언트가 연결된 상태인지 한번 더 확인
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
                    self.logger.debug(f"PCS 데이터 읽기 완료: {len(raw_data)}개 레지스터")
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
        PCS 원시 데이터를 가공합니다.
        
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
            
            # PCS 특화 계산
            self._calculate_derived_values(processed_data)
            
            self.logger.debug(f"PCS 데이터 가공 완료: {len(processed_data)}개 항목")
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
        # PCS 운전 모드 특별 처리 (비트 0, 1)
        if bit_num in [0, 1] and "운전 모드" in bit_desc:
            # 비트 0과 1을 조합하여 운전 모드 결정
            mode_bits = (raw_value >> 0) & 0x03  # 하위 2비트
            mode_descriptions = {
                0: "정지",
                1: "충전/정전압",
                2: "방전",
                3: "대기"
            }
            
            return {
                'mode_code': mode_bits,
                'mode_text': mode_descriptions.get(mode_bits, f"알 수 없음({mode_bits})"),
                'description': 'PCS 운전 모드',
                'status': mode_descriptions.get(mode_bits, f"알 수 없음({mode_bits})")
            }
        
        # 배터리 상태 특별 처리 (비트 3, 4)
        elif bit_num in [3, 4] and "Batt 상태" in bit_desc:
            # 비트 3과 4를 조합하여 배터리 상태 결정
            batt_bits = (raw_value >> 3) & 0x03  # 비트 3,4
            batt_descriptions = {
                0: "비활성",
                1: "충전",
                2: "방전",
                3: "알 수 없음"
            }
            
            return {
                'battery_code': batt_bits,
                'battery_text': batt_descriptions.get(batt_bits, f"알 수 없음({batt_bits})"),
                'description': '배터리 상태',
                'status': batt_descriptions.get(batt_bits, f"알 수 없음({batt_bits})")
            }
        
        # PCS 정상 상태 처리
        elif "정상 상태" in bit_desc:
            return {
                'status': '정상 상태' if is_set else '비정상 상태',
                'code': 1 if is_set else 0,
                'description': 'PCS 정상 상태'
            }
        
        # 독립운전모드 처리
        elif "독립운전모드" in bit_desc:
            return {
                'status': '독립운전' if is_set else '정지',
                'code': 1 if is_set else 0,
                'description': 'PCS 독립운전모드'
            }
        
        # Grid Black Out 처리
        elif "Grid Black Out" in bit_desc:
            return {
                'status': '계통 정전' if is_set else '계통 정상',
                'code': 1 if is_set else 0,
                'description': 'Grid Black Out'
            }
        
        # SOC 상태 처리
        elif "Empty Batt SOC" in bit_desc:
            return {
                'status': 'SOC 0%' if is_set else 'SOC 정상',
                'code': 1 if is_set else 0,
                'description': 'Empty Batt SOC'
            }
        elif "Full Batt SOC" in bit_desc:
            return {
                'status': 'SOC 100%' if is_set else 'SOC 정상',
                'code': 2 if is_set else 0,
                'description': 'Full Batt SOC'
            }
        
        # Remote Enable 처리
        elif "Remote Enable" in bit_desc:
            return {
                'status': '원격 제어' if is_set else '로컬 제어',
                'code': 1 if is_set else 0,
                'description': 'Remote Enable'
            }
        
        # MC 상태 처리
        elif "MC Close" in bit_desc:
            mc_type = "AC" if "AC MC" in bit_desc else "DC" if "DC MC" in bit_desc else "PR"
            return {
                'status': f'{mc_type} MC Close' if is_set else f'{mc_type} MC Open',
                'code': 1 if is_set else 0,
                'description': f'{mc_type} MC 상태'
            }
        
        # Total Fault 처리
        elif "Total Fault" in bit_desc:
            return {
                'status': '고장 발생' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': 'Total Fault'
            }
        
        # STATIC S/W 처리
        elif "STATIC S/W" in bit_desc:
            return {
                'status': 'STATIC S/W Close' if is_set else 'STATIC S/W Open',
                'code': 1 if is_set else 0,
                'description': 'STATIC S/W'
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
        
        # 기본 처리 - Reserved나 기타
        if "Reserved" in bit_desc:
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
        
        # PCS 운전 모드 종합 분석
        if "STATE1" in register_info.get('description', '') or "운전 모드" in register_info.get('description', ''):
            # 비트 0,1을 조합한 운전 모드
            mode_bits = (raw_value >> 0) & 0x03
            mode_descriptions = {
                0: "정지",
                1: "충전/정전압",
                2: "방전", 
                3: "대기"
            }
            
            additional_status['operating_mode'] = {
                'code': mode_bits,
                'text': mode_descriptions.get(mode_bits, f"알 수 없음({mode_bits})"),
                'description': 'PCS 운전 모드'
            }
            
            # 비트 2: PCS 정상 상태
            if bit_status.get('bit_02', {}).get('active', False):
                additional_status['pcs_status'] = {
                    'code': 1,
                    'text': '정상 상태',
                    'description': 'PCS 정상 상태'
                }
            else:
                additional_status['pcs_status'] = {
                    'code': 0,
                    'text': '비정상 상태',
                    'description': 'PCS 정상 상태'
                }
            
            # 비트 3,4: 배터리 상태
            batt_bits = (raw_value >> 3) & 0x03
            batt_descriptions = {
                0: "비활성",
                1: "충전",
                2: "방전",
                3: "알 수 없음"
            }
            
            additional_status['battery_status'] = {
                'code': batt_bits,
                'text': batt_descriptions.get(batt_bits, f"알 수 없음({batt_bits})"),
                'description': '배터리 상태'
            }
            
            # 비트 5: 독립운전모드
            if bit_status.get('bit_05', {}).get('active', False):
                additional_status['independent_mode'] = {
                    'code': 1,
                    'text': '독립운전',
                    'description': 'PCS 독립운전모드'
                }
            else:
                additional_status['independent_mode'] = {
                    'code': 0,
                    'text': '정지',
                    'description': 'PCS 독립운전모드'
                }
            
            # 비트 6: Grid Black Out
            if bit_status.get('bit_06', {}).get('active', False):
                additional_status['grid_status'] = {
                    'code': 1,
                    'text': '계통 정전',
                    'description': 'Grid Black Out'
                }
            else:
                additional_status['grid_status'] = {
                    'code': 0,
                    'text': '계통 정상',
                    'description': 'Grid Black Out'
                }
            
            # 비트 7: Empty Batt SOC
            if bit_status.get('bit_07', {}).get('active', False):
                additional_status['soc_status'] = {
                    'code': 1,
                    'text': 'SOC 0%',
                    'description': 'Empty Batt SOC'
                }
            # 비트 8: Full Batt SOC
            elif bit_status.get('bit_08', {}).get('active', False):
                additional_status['soc_status'] = {
                    'code': 2,
                    'text': 'SOC 100%',
                    'description': 'Full Batt SOC'
                }
            else:
                additional_status['soc_status'] = {
                    'code': 0,
                    'text': 'SOC 정상',
                    'description': 'SOC 상태'
                }
            
            # 비트 10: Remote Enable
            if bit_status.get('bit_10', {}).get('active', False):
                additional_status['control_mode'] = {
                    'code': 1,
                    'text': '원격 제어',
                    'description': 'Remote Enable'
                }
            else:
                additional_status['control_mode'] = {
                    'code': 0,
                    'text': '로컬 제어',
                    'description': 'Remote Enable'
                }
            
            # 비트 11-13: MC 상태들
            mc_status = {}
            if bit_status.get('bit_11', {}).get('active', False):
                mc_status['ac_mc'] = 'Close'
            else:
                mc_status['ac_mc'] = 'Open'
                
            if bit_status.get('bit_12', {}).get('active', False):
                mc_status['dc_mc'] = 'Close'
            else:
                mc_status['dc_mc'] = 'Open'
                
            if bit_status.get('bit_13', {}).get('active', False):
                mc_status['pr_mc'] = 'Close'
            else:
                mc_status['pr_mc'] = 'Open'
            
            additional_status['mc_status'] = mc_status
            
            # 비트 14: Total Fault
            if bit_status.get('bit_14', {}).get('active', False):
                additional_status['fault_status'] = {
                    'code': 1,
                    'text': '고장 발생',
                    'description': 'Total Fault'
                }
            else:
                additional_status['fault_status'] = {
                    'code': 0,
                    'text': '정상',
                    'description': 'Total Fault'
                }
            
            # 비트 15: STATIC S/W
            if bit_status.get('bit_15', {}).get('active', False):
                additional_status['static_switch'] = {
                    'code': 1,
                    'text': 'Close',
                    'description': 'STATIC S/W'
                }
            else:
                additional_status['static_switch'] = {
                    'code': 0,
                    'text': 'Open',
                    'description': 'STATIC S/W'
                }
        
        return additional_status
    
    def _calculate_derived_values(self, processed_data: Dict[str, Any]):
        """
        PCS 특화 계산값들을 추가합니다.
        
        Args:
            processed_data: 가공된 데이터 딕셔너리 (수정됨)
        """
        try:
            # 3상 전압 평균 계산
            if all(phase in processed_data for phase in ['ac_voltage_r', 'ac_voltage_s', 'ac_voltage_t']):
                avg_voltage = (
                    processed_data['ac_voltage_r']['value'] +
                    processed_data['ac_voltage_s']['value'] +
                    processed_data['ac_voltage_t']['value']
                ) / 3
                
                processed_data['avg_ac_voltage'] = {
                    'value': round(avg_voltage, 2),
                    'unit': 'V',
                    'description': '3상 AC 전압 평균',
                    'raw_value': avg_voltage
                }
            
            # 3상 전류 평균 계산
            if all(phase in processed_data for phase in ['ac_current_r', 'ac_current_s', 'ac_current_t']):
                avg_current = (
                    abs(processed_data['ac_current_r']['value']) +
                    abs(processed_data['ac_current_s']['value']) +
                    abs(processed_data['ac_current_t']['value'])
                ) / 3
                
                processed_data['avg_ac_current'] = {
                    'value': round(avg_current, 2),
                    'unit': 'A',
                    'description': '3상 AC 전류 평균 (절댓값)',
                    'raw_value': avg_current
                }
            
            # 전력 밀도 계산 (DC 전력 / DC 전압)
            if ('dc_power' in processed_data and 'dc_voltage' in processed_data and 
                processed_data['dc_voltage']['value'] > 0):
                
                power_density = processed_data['dc_power']['value'] / processed_data['dc_voltage']['value']
                processed_data['power_density'] = {
                    'value': round(power_density, 2),
                    'unit': 'W/V',
                    'description': '전력 밀도',
                    'raw_value': power_density
                }
            
            # PCS 효율 계산 (AC 전력 / DC 전력)
            if ('ac_power' in processed_data and 'dc_power' in processed_data and 
                processed_data['dc_power']['value'] != 0):
                
                # 방전 모드(DC->AC)와 충전 모드(AC->DC)에 따른 효율 계산
                if processed_data['dc_power']['value'] > 0:  # 방전 모드
                    efficiency = abs(processed_data['ac_power']['value']) / processed_data['dc_power']['value'] * 100
                else:  # 충전 모드
                    efficiency = abs(processed_data['dc_power']['value']) / abs(processed_data['ac_power']['value']) * 100
                
                processed_data['pcs_efficiency'] = {
                    'value': round(min(efficiency, 100), 2),  # 100% 초과 방지
                    'unit': '%',
                    'description': 'PCS 효율',
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
        PCS 운전 모드 설정
        
        Args:
            mode: 'stop'(정지), 'charge'(충전), 'discharge'(방전), 'standby'(대기) 중 하나
            
        Returns:
            성공 여부
        """
        mode_values = {
            'stop': 0,      # 정지
            'charge': 1,    # 충전
            'discharge': 2, # 방전
            'standby': 3    # 대기
        }
        
        if mode not in mode_values:
            self.logger.error(f"지원하지 않는 운전 모드: {mode}")
            return False
        
        return await self.write_register('operation_mode_control', mode_values[mode])
    
    async def set_power_reference(self, power_kw: float) -> bool:
        """
        PCS 출력 전력 설정점 설정
        
        Args:
            power_kw: 설정할 전력값 (kW)
            
        Returns:
            성공 여부
        """
        # 스케일 팩터 적용 (예: 0.1kW 단위로 저장)
        control_registers = self.device_map.get('control_registers', {})
        if 'power_reference' in control_registers:
            scale = control_registers['power_reference'].get('scale', 1)
            value = int(power_kw / scale)
            return await self.write_register('power_reference', value)
        else:
            self.logger.error("power_reference 레지스터를 찾을 수 없습니다")
            return False
    
    async def reset_faults(self) -> bool:
        """
        PCS 고장 리셋
        
        Returns:
            성공 여부
        """
        return await self.write_register('fault_reset', 1)
    
    async def handle_control_message(self, payload: Dict[str, Any]):
        """
        MQTT 제어 메시지를 처리합니다.
        지원 명령:
          - operation_mode : { "command": "operation_mode", "mode": "stop/charge/discharge/standby" }
          - power_reference : { "command": "power_reference", "power_kw": 10.5 }
          - reset_faults : { "command": "reset_faults" }
        """
        try:
            command = payload.get("command")
            
            if command == "operation_mode":
                mode = payload.get("mode")
                if mode:
                    result = await self.set_operation_mode(mode)
                    self.logger.info(f"PCS 운전 모드 설정 {'성공' if result else '실패'}: {mode}")
                else:
                    self.logger.warning("운전 모드가 지정되지 않았습니다")
            
            elif command == "power_reference":
                power_kw = payload.get("power_kw")
                if power_kw is not None:
                    result = await self.set_power_reference(float(power_kw))
                    self.logger.info(f"PCS 출력 전력 설정 {'성공' if result else '실패'}: {power_kw}kW")
                else:
                    self.logger.warning("출력 전력값이 지정되지 않았습니다")
            
            elif command == "reset_faults":
                result = await self.reset_faults()
                self.logger.info(f"PCS 고장 리셋 {'성공' if result else '실패'}")
            
            else:
                self.logger.warning(f"알 수 없는 PCS 제어 명령: {payload}")
                
        except Exception as e:
            self.logger.error(f"PCS 제어 메시지 처리 중 오류: {e}") 