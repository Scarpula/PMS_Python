#!/usr/bin/env python3
"""
PMS MQTT 메시지 추적기
pms/status/operation_mode 토픽의 메시지를 실시간으로 모니터링하고 추적합니다.
"""

import asyncio
import json
import time
from datetime import datetime
from paho.mqtt.client import Client as MQTTClient
import yaml
import sys
import os

class PmsMessageTracker:
    def __init__(self, config_path="config/config.yml"):
        self.config = self.load_config(config_path)
        self.mqtt_client = None
        
        # 운전 모드 상태 추적
        self.operation_mode_count = 0
        self.last_operation_message = None
        self.last_operation_timestamp = 0
        
        # 임계값 설정 추적
        self.threshold_config_count = 0
        self.last_threshold_message = None
        self.last_threshold_timestamp = 0
        
        # 추적할 토픽들
        self.topics = [
            "pms/status/operation_mode",
            "pms/status/threshold_config"
        ]
        
    def load_config(self, config_path):
        """설정 파일 로드"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"❌ 설정 파일 로드 실패: {e}")
            return None
    
    def on_connect(self, client, userdata, flags, rc):
        """MQTT 연결 콜백"""
        if rc == 0:
            print(f"✅ MQTT 브로커 연결 성공")
            for topic in self.topics:
                print(f"📡 토픽 구독: {topic}")
                client.subscribe(topic)
        else:
            print(f"❌ MQTT 연결 실패: {rc}")
    
    def on_message(self, client, userdata, msg):
        """MQTT 메시지 수신 콜백"""
        try:
            topic = msg.topic
            message_str = msg.payload.decode('utf-8')
            message_data = json.loads(message_str)
            current_time = datetime.now().strftime('%H:%M:%S')
            
            if topic == "pms/status/operation_mode":
                self.operation_mode_count += 1
                self.last_operation_message = message_data
                self.last_operation_timestamp = time.time()
                
                print(f"\n🔔 [{current_time}] 운전 모드 메시지 #{self.operation_mode_count} 수신")
                print(f"📍 토픽: {topic}")
                print(f"📊 메시지 크기: {len(message_str)} bytes")
                
                # 운전 모드 정보 추출
                current_mode = message_data.get('current_mode', 'N/A')
                auto_mode = message_data.get('auto_mode', {})
                auto_active = auto_mode.get('active', False)
                auto_state = auto_mode.get('current_state', 'N/A')
                last_soc = auto_mode.get('last_soc', 0)
                
                print(f"🎛️  현재 모드: {current_mode}")
                print(f"🤖 자동 모드: {'활성' if auto_active else '비활성'} ({auto_state})")
                print(f"🔋 마지막 SOC: {last_soc}%")
                
                # 설정 정보
                config = auto_mode.get('config', {})
                if config:
                    print(f"⚙️  임계값 설정:")
                    print(f"   - SOC 상한: {config.get('soc_high_threshold', 'N/A')}%")
                    print(f"   - SOC 하한: {config.get('soc_low_threshold', 'N/A')}%")
                    print(f"   - 충전정지: {config.get('soc_charge_stop_threshold', 'N/A')}%")
                
            elif topic == "pms/status/threshold_config":
                self.threshold_config_count += 1
                self.last_threshold_message = message_data
                self.last_threshold_timestamp = time.time()
                
                print(f"\n🔧 [{current_time}] 임계값 설정 메시지 #{self.threshold_config_count} 수신")
                print(f"📍 토픽: {topic}")
                print(f"📊 메시지 크기: {len(message_str)} bytes")
                
                # 임계값 정보 추출
                print(f"⚙️  임계값 설정:")
                print(f"   - SOC 상한: {message_data.get('soc_high_threshold', 'N/A')}%")
                print(f"   - SOC 하한: {message_data.get('soc_low_threshold', 'N/A')}%")
                print(f"   - 충전정지: {message_data.get('soc_charge_stop_threshold', 'N/A')}%")
                print(f"   - DCDC 대기: {message_data.get('dcdc_standby_time', 'N/A')}초")
                print(f"   - 충전전력: {message_data.get('charging_power', 'N/A')}kW")
                print(f"🎛️  운전 모드: {message_data.get('operation_mode', 'N/A')}")
                print(f"🤖 자동 상태: {message_data.get('auto_mode_status', 'N/A')}")
            
            print("-" * 60)
            
        except Exception as e:
            print(f"❌ 메시지 처리 오류: {e}")
    
    def on_disconnect(self, client, userdata, rc):
        """MQTT 연결 해제 콜백"""
        print(f"🔌 MQTT 연결 해제: {rc}")
    
    def print_status_info(self):
        """현재 상태 정보 출력"""
        current_time = datetime.now().strftime('%H:%M:%S')
        
        print(f"\n📊 [{current_time}] PMS 메시지 추적 상태")
        print(f"📡 구독 토픽: {', '.join(self.topics)}")
        
        # 운전 모드 상태
        operation_time_since_last = time.time() - self.last_operation_timestamp if self.last_operation_timestamp > 0 else 0
        print(f"\n🎛️  운전 모드 상태:")
        print(f"   📨 수신 메시지: {self.operation_mode_count}")
        print(f"   ⏰ 마지막 수신: {operation_time_since_last:.1f}초 전")
        
        if self.last_operation_message:
            current_mode = self.last_operation_message.get('current_mode', 'N/A')
            auto_mode = self.last_operation_message.get('auto_mode', {})
            auto_active = auto_mode.get('active', False)
            auto_state = auto_mode.get('current_state', 'N/A')
            
            print(f"   현재 모드: {current_mode}")
            print(f"   자동 모드: {'활성' if auto_active else '비활성'} ({auto_state})")
        else:
            print("   ❌ 메시지 없음")
        
        # 임계값 설정 상태
        threshold_time_since_last = time.time() - self.last_threshold_timestamp if self.last_threshold_timestamp > 0 else 0
        print(f"\n⚙️  임계값 설정 상태:")
        print(f"   📨 수신 메시지: {self.threshold_config_count}")
        print(f"   ⏰ 마지막 수신: {threshold_time_since_last:.1f}초 전")
        
        if self.last_threshold_message:
            print(f"   SOC 상한: {self.last_threshold_message.get('soc_high_threshold', 'N/A')}%")
            print(f"   SOC 하한: {self.last_threshold_message.get('soc_low_threshold', 'N/A')}%")
            print(f"   운전 모드: {self.last_threshold_message.get('operation_mode', 'N/A')}")
        else:
            print("   ❌ 메시지 없음")
        
        print("-" * 60)
    
    def start_monitoring(self):
        """모니터링 시작"""
        if not self.config:
            print("❌ 설정 파일이 없어 모니터링을 시작할 수 없습니다.")
            return
        
        # MQTT 클라이언트 설정
        mqtt_config = self.config.get('mqtt', {})
        broker_host = mqtt_config.get('host', 'localhost')
        broker_port = mqtt_config.get('port', 1883)
        
        print(f"🚀 PMS MQTT 메시지 추적기 시작")
        print(f"🌐 브로커: {broker_host}:{broker_port}")
        print(f"📡 추적 토픽: {', '.join(self.topics)}")
        print("=" * 60)
        
        # MQTT 클라이언트 생성 및 설정
        self.mqtt_client = MQTTClient()
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_disconnect = self.on_disconnect
        
        try:
            # MQTT 브로커 연결
            self.mqtt_client.connect(broker_host, broker_port, 60)
            
            # 메시지 루프 시작 (백그라운드)
            self.mqtt_client.loop_start()
            
            print("💡 명령어:")
            print("  - 's' + Enter: 현재 상태 출력")
            print("  - 'q' + Enter: 종료")
            print("  - Enter만: 계속 모니터링")
            print("-" * 60)
            
            # 사용자 입력 대기
            while True:
                try:
                    user_input = input().strip().lower()
                    
                    if user_input == 'q':
                        print("👋 모니터링을 종료합니다.")
                        break
                    elif user_input == 's':
                        self.print_status_info()
                    else:
                        # 아무 입력이 없으면 계속 모니터링
                        pass
                        
                except KeyboardInterrupt:
                    print("\n👋 Ctrl+C로 종료합니다.")
                    break
            
        except Exception as e:
            print(f"❌ 모니터링 중 오류 발생: {e}")
        
        finally:
            if self.mqtt_client:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
                print("🔌 MQTT 연결 종료")

def main():
    """메인 함수"""
    print("🎯 PMS MQTT 메시지 추적기")
    print("=" * 60)
    
    # 설정 파일 경로 확인
    config_path = "config/config.yml"
    if not os.path.exists(config_path):
        print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
        print("💡 PMS 디렉토리에서 실행해주세요.")
        sys.exit(1)
    
    # 추적기 시작
    tracker = PmsMessageTracker(config_path)
    tracker.start_monitoring()

if __name__ == "__main__":
    main() 