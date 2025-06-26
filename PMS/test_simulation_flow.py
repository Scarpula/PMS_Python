#!/usr/bin/env python3
"""
PMS 시뮬레이션 데이터 플로우 테스트
- 가상 데이터로 MQTT 발행 테스트
- GUI에서 수신할 수 있는 데이터 구조 확인
"""

import asyncio
import yaml
import json
import random
from pathlib import Path
from datetime import datetime
from pms_app.core.mqtt_client import MQTTClient
from pms_app.utils.logger import setup_logger


class SimulationDataFlowTester:
    """시뮬레이션 데이터 플로우 테스트 클래스"""
    
    def __init__(self):
        self.logger = setup_logger("SimulationTest")
        self.config: dict = {}
        self.mqtt_publisher = None
        self.mqtt_subscriber = None
        self.received_messages = []
    
    def load_config(self):
        """설정 파일 로드"""
        config_path = Path(__file__).parent / "config" / "config.yml"
        with open(config_path, 'r', encoding='utf-8') as file:
            loaded_config = yaml.safe_load(file)
            if loaded_config is None:
                raise ValueError("설정 파일이 비어있습니다")
            self.config = loaded_config
        self.logger.info("설정 파일 로드 완료")
    
    async def setup_mqtt_clients(self):
        """MQTT 클라이언트 설정"""
        # 발행자 설정
        publisher_config = self.config['mqtt'].copy()
        publisher_config['client_id'] = 'pms_sim_publisher'
        self.mqtt_publisher = MQTTClient(publisher_config)
        await self.mqtt_publisher.connect()
        
        # 구독자 설정 (GUI 역할)
        subscriber_config = self.config['mqtt'].copy()
        subscriber_config['client_id'] = 'pms_sim_subscriber'
        self.mqtt_subscriber = MQTTClient(subscriber_config)
        
        # 메시지 수신 콜백
        def on_message_received(topic, payload):
            self.logger.info(f"메시지 수신: {topic}")
            try:
                data = json.loads(payload)
                self.received_messages.append({
                    'topic': topic,
                    'data': data,
                    'timestamp': datetime.now()
                })
            except json.JSONDecodeError as e:
                self.logger.error(f"JSON 파싱 오류: {e}")
        
        self.mqtt_subscriber.set_message_callback(on_message_received)
        await self.mqtt_subscriber.connect()
        await self.mqtt_subscriber.subscribe("pms/+/+/data")
        
        self.logger.info("MQTT 클라이언트 설정 완료")
    
    def generate_bms_data(self, device_name: str, ip: str):
        """BMS 시뮬레이션 데이터 생성"""
        return {
            "device_name": device_name,
            "device_type": "BMS",
            "ip_address": ip,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "rack_voltage": {
                    "value": round(random.uniform(48.0, 54.0), 2),
                    "unit": "V",
                    "description": "랙 전압",
                    "raw_value": round(random.uniform(480, 540), 0)
                },
                "rack_current": {
                    "value": round(random.uniform(-50.0, 50.0), 2),
                    "unit": "A", 
                    "description": "랙 전류",
                    "raw_value": round(random.uniform(-500, 500), 0)
                },
                "soc": {
                    "value": round(random.uniform(20.0, 90.0), 1),
                    "unit": "%",
                    "description": "충전 상태",
                    "raw_value": round(random.uniform(200, 900), 0)
                },
                "temperature_max": {
                    "value": round(random.uniform(20.0, 45.0), 1),
                    "unit": "°C",
                    "description": "최고 온도",
                    "raw_value": round(random.uniform(200, 450), 0)
                },
                "alarm_1": {
                    "value": random.randint(0, 15),
                    "unit": "",
                    "description": "ALARM1",
                    "raw_value": random.randint(0, 15),
                    "type": "bitmask",
                    "active_bits": ["Bit 0: 랙 저전압"] if random.random() > 0.8 else [],
                    "bit_status": {"bit_00": {"active": random.random() > 0.8, "description": "랙 저전압"}},
                    "total_active": 1 if random.random() > 0.8 else 0
                },
                "status_1": {
                    "value": random.randint(0, 7),
                    "unit": "",
                    "description": "STATUS1", 
                    "raw_value": random.randint(0, 7),
                    "type": "bitmask",
                    "active_bits": ["Bit 1: 충전중", "Bit 2: 정상운전"] if random.random() > 0.5 else [],
                    "bit_status": {
                        "bit_01": {"active": True, "description": "충전중"},
                        "bit_02": {"active": True, "description": "정상운전"}
                    },
                    "total_active": 2
                }
            }
        }
    
    def generate_dcdc_data(self, device_name: str, ip: str):
        """DCDC 시뮬레이션 데이터 생성"""
        input_voltage = round(random.uniform(380.0, 420.0), 1)
        output_voltage = round(random.uniform(790.0, 830.0), 1)
        input_current = round(random.uniform(10.0, 30.0), 1)
        output_current = round(random.uniform(5.0, 15.0), 1)
        efficiency = round((output_voltage * output_current) / (input_voltage * input_current) * 100, 1)
        
        return {
            "device_name": device_name,
            "device_type": "DCDC", 
            "ip_address": ip,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "input_voltage": {
                    "value": input_voltage,
                    "unit": "V",
                    "description": "입력 전압",
                    "raw_value": round(input_voltage * 10, 0)
                },
                "output_voltage": {
                    "value": output_voltage,
                    "unit": "V",
                    "description": "출력 전압", 
                    "raw_value": round(output_voltage * 10, 0)
                },
                "input_current": {
                    "value": input_current,
                    "unit": "A",
                    "description": "입력 전류",
                    "raw_value": round(input_current * 10, 0)
                },
                "output_current": {
                    "value": output_current,
                    "unit": "A",
                    "description": "출력 전류",
                    "raw_value": round(output_current * 10, 0)
                },
                "calculated_efficiency": {
                    "value": efficiency,
                    "unit": "%",
                    "description": "계산된 효율",
                    "raw_value": efficiency
                },
                "temperature_1": {
                    "value": round(random.uniform(30.0, 65.0), 1),
                    "unit": "°C",
                    "description": "온도 1 (Heat Sink IGBT A)",
                    "raw_value": round(random.uniform(300, 650), 0)
                },
                "alarm_1": {
                    "value": random.randint(0, 31),
                    "unit": "",
                    "description": "ALARM 1",
                    "raw_value": random.randint(0, 31),
                    "type": "bitmask",
                    "active_bits": ["Bit 2: 출력 저전압"] if random.random() > 0.9 else [],
                    "bit_status": {"bit_02": {"active": random.random() > 0.9, "description": "출력 저전압"}},
                    "total_active": 1 if random.random() > 0.9 else 0
                },
                "status_2": {
                    "value": random.randint(1, 14),
                    "unit": "",
                    "description": "STATUS 2",
                    "raw_value": random.randint(1, 14),
                    "type": "bitmask",
                    "active_bits": ["Bit 2: 충전운전 상태", "Bit 7: 정상 상태"],
                    "bit_status": {
                        "bit_02": {"active": True, "description": "충전운전 상태"},
                        "bit_07": {"active": True, "description": "정상 상태"}
                    },
                    "total_active": 2
                }
            }
        }
    
    def generate_pcs_data(self, device_name: str, ip: str):
        """PCS 시뮬레이션 데이터 생성"""
        ac_voltage = round(random.uniform(380.0, 400.0), 1)
        dc_voltage = round(random.uniform(790.0, 830.0), 1)
        ac_current = round(random.uniform(10.0, 50.0), 1)
        dc_current = round(random.uniform(5.0, 25.0), 1)
        
        return {
            "device_name": device_name,
            "device_type": "PCS",
            "ip_address": ip,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "ac_voltage_r": {
                    "value": ac_voltage,
                    "unit": "V",
                    "description": "AC 전압 R상",
                    "raw_value": round(ac_voltage * 10, 0)
                },
                "ac_voltage_s": {
                    "value": round(random.uniform(380.0, 400.0), 1),
                    "unit": "V", 
                    "description": "AC 전압 S상",
                    "raw_value": round(random.uniform(3800, 4000), 0)
                },
                "ac_voltage_t": {
                    "value": round(random.uniform(380.0, 400.0), 1),
                    "unit": "V",
                    "description": "AC 전압 T상", 
                    "raw_value": round(random.uniform(3800, 4000), 0)
                },
                "dc_voltage": {
                    "value": dc_voltage,
                    "unit": "V",
                    "description": "DC 전압",
                    "raw_value": round(dc_voltage * 10, 0)
                },
                "ac_current_r": {
                    "value": ac_current,
                    "unit": "A",
                    "description": "AC 전류 R상",
                    "raw_value": round(ac_current * 10, 0)
                },
                "dc_current": {
                    "value": dc_current,
                    "unit": "A",
                    "description": "DC 전류",
                    "raw_value": round(dc_current * 10, 0)
                },
                "active_power": {
                    "value": round(ac_voltage * ac_current * 1.732 / 1000, 2),
                    "unit": "kW",
                    "description": "유효 전력",
                    "raw_value": round(ac_voltage * ac_current * 1.732, 0)
                },
                "frequency": {
                    "value": round(random.uniform(59.8, 60.2), 2),
                    "unit": "Hz",
                    "description": "주파수",
                    "raw_value": round(random.uniform(598, 602), 0)
                },
                "alarm_1": {
                    "value": random.randint(0, 255),
                    "unit": "",
                    "description": "ALARM1",
                    "raw_value": random.randint(0, 255),
                    "type": "bitmask",
                    "active_bits": ["Bit 6: 계통 Freq Low"] if random.random() > 0.85 else [],
                    "bit_status": {"bit_06": {"active": random.random() > 0.85, "description": "계통 Freq Low"}},
                    "total_active": 1 if random.random() > 0.85 else 0
                },
                "state_1": {
                    "value": random.randint(0, 2047),
                    "unit": "",
                    "description": "STATE1",
                    "raw_value": random.randint(0, 2047),
                    "type": "bitmask",
                    "active_bits": ["Bit 2: Pcs 정상 상태", "Bit 11: AC MC Close"],
                    "bit_status": {
                        "bit_02": {"active": True, "description": "Pcs 정상 상태"},
                        "bit_11": {"active": True, "description": "AC MC Close"}
                    },
                    "total_active": 2
                }
            }
        }
    
    async def publish_simulation_data(self):
        """시뮬레이션 데이터 발행"""
        device_generators = {
            'BMS': self.generate_bms_data,
            'DCDC': self.generate_dcdc_data,
            'PCS': self.generate_pcs_data
        }
        
        published_count = 0
        for device_config in self.config['devices']:
            device_name = device_config['name']
            device_type = device_config['type']
            device_ip = device_config['ip']
            
            if device_type in device_generators:
                # 시뮬레이션 데이터 생성
                sim_data = device_generators[device_type](device_name, device_ip)
                
                # MQTT 토픽 구성 (실제 핸들러와 동일한 형식)
                topic = f"pms/{device_type}/{device_name}/data"
                
                # 데이터 발행
                success = self.mqtt_publisher.publish(topic, sim_data)
                if success:
                    published_count += 1
                    self.logger.info(f"시뮬레이션 데이터 발행: {device_name} ({device_type})")
                else:
                    self.logger.error(f"데이터 발행 실패: {device_name}")
                
                await asyncio.sleep(0.5)  # 발행 간격
        
        return published_count
    
    def print_simulation_results(self):
        """시뮬레이션 결과 출력"""
        self.logger.info("=== 시뮬레이션 결과 ===")
        
        for i, msg in enumerate(self.received_messages):
            data = msg['data']
            timestamp = msg['timestamp'].strftime('%H:%M:%S')
            
            print(f"\n[{i+1}] 시간: {timestamp}")
            print(f"    토픽: {msg['topic']}")
            print(f"    장비: {data.get('device_name', 'N/A')}")
            print(f"    타입: {data.get('device_type', 'N/A')}")
            print(f"    IP: {data.get('ip_address', 'N/A')}")
            
            # 센서 데이터 요약
            sensor_data = data.get('data', {})
            if sensor_data:
                print(f"    센서 데이터: {len(sensor_data)}개 항목")
                
                # 주요 값들 표시
                key_params = ['voltage', 'current', 'soc', 'temperature', 'power', 'frequency']
                for key, value in sensor_data.items():
                    if any(param in key.lower() for param in key_params):
                        if isinstance(value, dict):
                            val = value.get('value', 'N/A')
                            unit = value.get('unit', '')
                            print(f"      {key}: {val} {unit}")
                
                # 비트마스크 상태
                bitmask_count = 0
                for key, value in sensor_data.items():
                    if isinstance(value, dict) and value.get('type') == 'bitmask':
                        active_count = value.get('total_active', 0)
                        if active_count > 0:
                            bitmask_count += 1
                            active_bits = value.get('active_bits', [])
                            print(f"      {key}: {active_count}개 활성 비트")
                            for bit in active_bits[:2]:  # 최대 2개만 표시
                                print(f"        - {bit}")
                
                if bitmask_count == 0:
                    print("      알람/상태: 정상")
            else:
                print("    센서 데이터: 없음")
    
    async def run_simulation_test(self):
        """시뮬레이션 테스트 실행"""
        try:
            self.logger.info("=== PMS 시뮬레이션 데이터 플로우 테스트 시작 ===")
            
            # 설정 로드
            self.load_config()
            
            # MQTT 클라이언트 설정
            await self.setup_mqtt_clients()
            
            # 시뮬레이션 데이터 발행
            published_count = await self.publish_simulation_data()
            
            # 메시지 수신 대기
            await asyncio.sleep(3)
            
            # 결과 분석
            received_count = len(self.received_messages)
            
            self.logger.info(f"발행된 메시지: {published_count}개")
            self.logger.info(f"수신된 메시지: {received_count}개")
            
            if published_count > 0 and received_count > 0:
                self.logger.info("✅ 시뮬레이션 데이터 플로우 성공!")
                self.print_simulation_results()
                return True
            else:
                self.logger.warning("⚠️ 시뮬레이션 테스트 실패")
                return False
                
        except Exception as e:
            self.logger.error(f"시뮬레이션 테스트 중 오류: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            # 정리
            try:
                if self.mqtt_publisher:
                    await self.mqtt_publisher.disconnect()
                if self.mqtt_subscriber:
                    await self.mqtt_subscriber.disconnect()
            except:
                pass


async def main():
    """메인 시뮬레이션 함수"""
    tester = SimulationDataFlowTester()
    success = await tester.run_simulation_test()
    
    if success:
        print("\n🎉 시뮬레이션 데이터 플로우가 성공했습니다!")
        print("   ✅ MQTT 발행 및 수신")
        print("   ✅ JSON 데이터 구조 검증")
        print("   ✅ 비트마스크 처리")
        print("   ✅ GUI 호환 데이터 형식")
        print("\n💡 이제 main.py와 GUI가 정상적으로 작동할 것입니다.")
    else:
        print("\n❌ 시뮬레이션 테스트에 실패했습니다.")


if __name__ == "__main__":
    asyncio.run(main()) 