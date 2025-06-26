#!/usr/bin/env python3
"""
PMS 데이터 플로우 테스트 스크립트
- 백그라운드 서버(main.py)와 GUI 간의 데이터 흐름 확인
- MQTT 발행 및 수신 테스트
"""

import asyncio
import yaml
import json
import time
from pathlib import Path
from datetime import datetime

# PMS 모듈 임포트
from pms_app.core.mqtt_client import MQTTClient
from pms_app.devices import DeviceFactory
from pms_app.utils.logger import setup_logger


class DataFlowTester:
    """데이터 플로우 테스트 클래스"""
    
    def __init__(self):
        self.logger = setup_logger("DataFlowTest")
        self.config: dict = {}
        self.mqtt_client = None
        self.device_handlers = []
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
    
    async def setup_mqtt_subscriber(self):
        """MQTT 구독자 설정 (GUI 역할)"""
        try:
            # 별도의 MQTT 클라이언트로 구독
            subscriber_config = self.config['mqtt'].copy()
            subscriber_config['client_id'] = 'pms_gui_test'
            
            self.mqtt_subscriber = MQTTClient(subscriber_config)
            
            # 메시지 수신 콜백 설정
            def on_message_received(topic, payload):
                self.logger.info(f"수신된 메시지 - 토픽: {topic}")
                try:
                    data = json.loads(payload)
                    self.received_messages.append({
                        'topic': topic,
                        'data': data,
                        'timestamp': datetime.now()
                    })
                    self.logger.info(f"데이터 파싱 성공: {data.get('device_name', 'Unknown')}")
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSON 파싱 오류: {e}")
            
            self.mqtt_subscriber.set_message_callback(on_message_received)
            await self.mqtt_subscriber.connect()
            
            # 모든 PMS 토픽 구독
            await self.mqtt_subscriber.subscribe("pms/+/+/data")
            self.logger.info("MQTT 구독자 설정 완료")
            
        except Exception as e:
            self.logger.error(f"MQTT 구독자 설정 오류: {e}")
            raise
    
    async def setup_device_handlers(self):
        """장비 핸들러 설정 (백그라운드 서버 역할)"""
        try:
            # 발행용 MQTT 클라이언트
            publisher_config = self.config['mqtt'].copy()
            publisher_config['client_id'] = 'pms_publisher_test'
            
            self.mqtt_client = MQTTClient(publisher_config)
            await self.mqtt_client.connect()
            
            # 장비 핸들러 생성
            for device_config in self.config['devices']:
                handler = DeviceFactory.create_device(device_config, self.mqtt_client)
                self.device_handlers.append(handler)
                self.logger.info(f"장비 핸들러 생성: {device_config['name']} ({device_config['type']})")
            
            self.logger.info("장비 핸들러 설정 완료")
            
        except Exception as e:
            self.logger.error(f"장비 핸들러 설정 오류: {e}")
            raise
    
    async def test_single_device_poll(self, device_name=None):
        """단일 장비 폴링 테스트"""
        target_handler = None
        
        if device_name:
            for handler in self.device_handlers:
                if handler.name == device_name:
                    target_handler = handler
                    break
        else:
            target_handler = self.device_handlers[0] if self.device_handlers else None
        
        if not target_handler:
            self.logger.error("테스트할 장비 핸들러를 찾을 수 없습니다")
            return False
        
        self.logger.info(f"장비 폴링 테스트 시작: {target_handler.name}")
        
        try:
            # 데이터 읽기 테스트
            raw_data = await target_handler.read_data()
            if raw_data:
                self.logger.info(f"데이터 읽기 성공: {len(raw_data)}개 항목")
                
                # 데이터 가공 테스트
                processed_data = await target_handler.process_data(raw_data)
                if processed_data:
                    self.logger.info(f"데이터 가공 성공: {len(processed_data)}개 항목")
                    
                    # MQTT 발행 테스트
                    await target_handler.publish_data(processed_data)
                    self.logger.info("MQTT 발행 완료")
                    
                    return True
                else:
                    self.logger.warning("데이터 가공 결과가 비어있습니다")
            else:
                self.logger.warning("데이터 읽기 결과가 비어있습니다")
            
        except Exception as e:
            self.logger.error(f"장비 폴링 테스트 오류: {e}")
        
        return False
    
    async def test_mqtt_flow(self):
        """MQTT 메시지 송수신 플로우 테스트"""
        self.logger.info("MQTT 플로우 테스트 시작")
        
        # 구독자 메시지 카운트 초기화
        initial_count = len(self.received_messages)
        
        # 모든 장비에 대해 폴링 실행
        success_count = 0
        for handler in self.device_handlers:
            try:
                self.logger.info(f"폴링 테스트: {handler.name}")
                if await self.test_single_device_poll(handler.name):
                    success_count += 1
                
                # 메시지 처리 시간 대기
                await asyncio.sleep(1)
                
            except Exception as e:
                self.logger.error(f"장비 {handler.name} 폴링 오류: {e}")
        
        # 메시지 수신 확인
        await asyncio.sleep(3)  # 메시지 수신 대기
        
        received_count = len(self.received_messages) - initial_count
        self.logger.info(f"폴링 성공: {success_count}/{len(self.device_handlers)}")
        self.logger.info(f"수신된 메시지: {received_count}개")
        
        return success_count, received_count
    
    def print_received_data(self):
        """수신된 데이터 출력"""
        self.logger.info("=== 수신된 데이터 요약 ===")
        
        for i, msg in enumerate(self.received_messages[-5:]):  # 최근 5개만 출력
            data = msg['data']
            timestamp = msg['timestamp'].strftime('%H:%M:%S')
            
            print(f"\n[{i+1}] 시간: {timestamp}")
            print(f"    토픽: {msg['topic']}")
            print(f"    장비: {data.get('device_name', 'N/A')}")
            print(f"    타입: {data.get('device_type', 'N/A')}")
            print(f"    IP: {data.get('ip_address', 'N/A')}")
            
            # 실제 센서 데이터 확인
            sensor_data = data.get('data', {})
            if sensor_data:
                print(f"    센서 데이터: {len(sensor_data)}개 항목")
                # 주요 항목 몇 개만 출력
                for j, (key, value) in enumerate(list(sensor_data.items())[:3]):
                    if isinstance(value, dict):
                        val = value.get('value', 'N/A')
                        unit = value.get('unit', '')
                        desc = value.get('description', '')
                        print(f"      {key}: {val} {unit} ({desc})")
                    else:
                        print(f"      {key}: {value}")
                if len(sensor_data) > 3:
                    print(f"      ... 외 {len(sensor_data)-3}개 항목")
            else:
                print("    센서 데이터: 없음")
    
    async def run_test(self):
        """전체 테스트 실행"""
        try:
            self.logger.info("=== PMS 데이터 플로우 테스트 시작 ===")
            
            # 1. 설정 로드
            self.load_config()
            
            # 2. MQTT 구독자 설정 (GUI 역할)
            await self.setup_mqtt_subscriber()
            
            # 3. 장비 핸들러 설정 (백그라운드 서버 역할)
            await self.setup_device_handlers()
            
            # 4. MQTT 플로우 테스트
            success_count, received_count = await self.test_mqtt_flow()
            
            # 5. 결과 출력
            self.print_received_data()
            
            # 6. 전체 결과 요약
            self.logger.info("=== 테스트 결과 요약 ===")
            self.logger.info(f"설정된 장비 수: {len(self.device_handlers)}")
            self.logger.info(f"폴링 성공 장비: {success_count}")
            self.logger.info(f"수신된 MQTT 메시지: {received_count}")
            
            if success_count > 0 and received_count > 0:
                self.logger.info("✅ 데이터 플로우 테스트 성공!")
                return True
            else:
                self.logger.warning("⚠️ 일부 테스트 실패")
                return False
                
        except Exception as e:
            self.logger.error(f"테스트 실행 중 오류: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        finally:
            # 정리
            try:
                if hasattr(self, 'mqtt_client') and self.mqtt_client:
                    await self.mqtt_client.disconnect()
                if hasattr(self, 'mqtt_subscriber') and self.mqtt_subscriber:
                    await self.mqtt_subscriber.disconnect()
            except:
                pass


async def main():
    """메인 테스트 함수"""
    tester = DataFlowTester()
    success = await tester.run_test()
    
    if success:
        print("\n🎉 전체 데이터 플로우가 정상적으로 작동합니다!")
        print("   - 장비에서 데이터 읽기 ✅")
        print("   - 데이터 가공 ✅") 
        print("   - MQTT 발행 ✅")
        print("   - MQTT 수신 ✅")
        print("\n💡 이제 GUI에서 실시간 데이터를 볼 수 있습니다.")
    else:
        print("\n❌ 데이터 플로우에 문제가 있습니다.")
        print("   설정 파일과 네트워크 연결을 확인해주세요.")


if __name__ == "__main__":
    asyncio.run(main()) 