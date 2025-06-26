#!/usr/bin/env python3
"""
PMS MQTT 메시지 모니터링 스크립트
- 백그라운드 서버가 발행하는 메시지 실시간 모니터링
"""

import asyncio
import yaml
import json
from pathlib import Path
from datetime import datetime
from pms_app.core.mqtt_client import MQTTClient
from pms_app.utils.logger import setup_logger


class MQTTMonitor:
    """MQTT 메시지 모니터링 클래스"""
    
    def __init__(self):
        self.logger = setup_logger("MQTTMonitor")
        self.config: dict = {}
        self.mqtt_client = None
        self.message_count = 0
        self.running = True
    
    def load_config(self):
        """설정 파일 로드"""
        config_path = Path(__file__).parent / "config" / "config.yml"
        with open(config_path, 'r', encoding='utf-8') as file:
            loaded_config = yaml.safe_load(file)
            if loaded_config is None:
                raise ValueError("설정 파일이 비어있습니다")
            self.config = loaded_config
        self.logger.info("설정 파일 로드 완료")
    
    def on_message_received(self, topic, payload):
        """메시지 수신 콜백"""
        self.message_count += 1
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        
        try:
            data = json.loads(payload)
            device_name = data.get('device_name', 'Unknown')
            device_type = data.get('device_type', 'Unknown')
            
            print(f"[{timestamp}] #{self.message_count}")
            print(f"  토픽: {topic}")
            print(f"  장비: {device_name} ({device_type})")
            
            # 실제 센서 데이터 확인
            sensor_data = data.get('data', {})
            if sensor_data:
                # 주요 파라미터만 출력
                key_values = []
                for key, value in sensor_data.items():
                    if isinstance(value, dict) and 'value' in value:
                        val = value['value']
                        unit = value.get('unit', '')
                        if 'voltage' in key.lower() or 'current' in key.lower():
                            key_values.append(f"{key}: {val} {unit}")
                        elif 'soc' in key.lower() or 'temperature' in key.lower():
                            key_values.append(f"{key}: {val} {unit}")
                        elif 'power' in key.lower() or 'frequency' in key.lower():
                            key_values.append(f"{key}: {val} {unit}")
                
                if key_values:
                    print(f"  주요 데이터: {', '.join(key_values[:3])}")
                
                # 알람/상태 확인
                alarm_status = []
                for key, value in sensor_data.items():
                    if isinstance(value, dict) and value.get('type') == 'bitmask':
                        active_count = value.get('total_active', 0)
                        if active_count > 0:
                            alarm_status.append(f"{key}({active_count})")
                
                if alarm_status:
                    print(f"  활성 알람/상태: {', '.join(alarm_status)}")
                else:
                    print(f"  상태: 정상 ({len(sensor_data)}개 파라미터)")
            else:
                print(f"  센서 데이터: 없음")
            
            print()  # 빈 줄
            
        except json.JSONDecodeError as e:
            print(f"[{timestamp}] JSON 파싱 오류: {e}")
        except Exception as e:
            print(f"[{timestamp}] 메시지 처리 오류: {e}")
    
    async def start_monitoring(self):
        """모니터링 시작"""
        try:
            self.load_config()
            
            # MQTT 클라이언트 설정
            subscriber_config = self.config['mqtt'].copy()
            subscriber_config['client_id'] = 'pms_monitor'
            
            self.mqtt_client = MQTTClient(subscriber_config)
            self.mqtt_client.set_message_callback(self.on_message_received)
            
            await self.mqtt_client.connect()
            await self.mqtt_client.subscribe("pms/+/+/data")
            
            print("=" * 60)
            print("PMS MQTT 메시지 모니터링 시작")
            print("백그라운드 서버의 데이터 발행을 실시간 모니터링합니다")
            print("종료하려면 Ctrl+C를 누르세요")
            print("=" * 60)
            print()
            
            # 모니터링 루프
            while self.running:
                await asyncio.sleep(1)
                
                # 주기적으로 연결 상태 확인
                if self.message_count % 30 == 0 and self.message_count > 0:
                    print(f"[정보] 지금까지 {self.message_count}개 메시지 수신됨")
                    print()
        
        except KeyboardInterrupt:
            print("\n모니터링 종료됨")
        except Exception as e:
            self.logger.error(f"모니터링 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.mqtt_client:
                await self.mqtt_client.disconnect()


async def main():
    """메인 모니터링 함수"""
    monitor = MQTTMonitor()
    await monitor.start_monitoring()


if __name__ == "__main__":
    asyncio.run(main()) 