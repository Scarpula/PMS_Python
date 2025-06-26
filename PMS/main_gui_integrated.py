"""
PMS GUI + 서버 통합 실행 스크립트
GUI 인터페이스와 백그라운드 PMS 서버를 동시에 실행합니다.
"""

import asyncio
import threading
import yaml
import sys
import time
from pathlib import Path
from typing import Optional

from pms_app.gui.main_window import PMSMainWindow
from pms_app.core.scheduler import PMSScheduler
from pms_app.core.mqtt_client import MQTTClient
from pms_app.core.data_manager import data_manager
from pms_app.devices import DeviceFactory
from pms_app.automation import OperationManager
from pms_app.utils.logger import setup_logger


class IntegratedPMSApp:
    """GUI + 서버 통합 PMS 애플리케이션"""
    
    def __init__(self):
        self.config = None
        self.logger = None
        self.mqtt_client = None
        self.scheduler = None
        self.device_handlers = []
        self.device_handler_map = {}
        self.operation_manager = None
        self.server_running = False
        self.server_thread = None
        self.gui_app = None
    
    def load_config(self):
        """설정 파일을 로드합니다."""
        config_path = Path(__file__).parent / "config" / "config.yml"
        try:
            with open(config_path, 'r', encoding='utf-8') as file:
                self.config = yaml.safe_load(file)
                print(f"✅ 설정 파일 로드 완료: {config_path}")
                return True
        except FileNotFoundError:
            print(f"❌ 설정 파일을 찾을 수 없습니다: {config_path}")
            # 기본 설정 사용
            self.config = {
                'mqtt': {
                    'broker': 'localhost',
                    'port': 1883,
                    'client_id': 'pms_integrated_client'
                },
                'devices': [
                    {
                        'name': 'Rack1_BMS',
                        'type': 'BMS',
                        'ip': '192.168.1.10',
                        'poll_interval': 2
                    },
                    {
                        'name': 'Farm_DCDC',
                        'type': 'DCDC',
                        'ip': '192.168.1.20',
                        'poll_interval': 1
                    },
                    {
                        'name': 'Unit1_PCS',
                        'type': 'PCS',
                        'ip': '192.168.1.30',
                        'poll_interval': 3
                    }
                ]
            }
            print("🔧 기본 설정을 사용합니다.")
            return True
        except yaml.YAMLError as e:
            print(f"❌ 설정 파일 파싱 오류: {e}")
            return False
    
    async def start_pms_server(self):
        """백그라운드 PMS 서버 시작"""
        print("\n🚀 PMS 서버 시작 중...")
        
        try:
            # 로거 설정
            self.logger = setup_logger("PMS_Integrated")
            self.logger.info("통합 PMS 애플리케이션 시작")
            
            # MQTT 클라이언트 초기화
            if self.config:
                print("🔌 MQTT 클라이언트 초기화 중...")
                self.mqtt_client = MQTTClient(self.config['mqtt'])
                print("🔗 MQTT 브로커 연결 시도...")
                await self.mqtt_client.connect()
                print("✅ MQTT 클라이언트 연결 완료")
                
                # MQTT 클라이언트 상태 확인
                if hasattr(self.mqtt_client, 'log_status'):
                    self.mqtt_client.log_status()
                
                # 데이터 매니저에 MQTT 클라이언트 설정
                data_manager.set_mqtt_client(self.mqtt_client)
                
                # 시스템 설정 추출
                system_config = self.config.get('system', {})
                
                # 장비 핸들러 생성
                print("🔧 장비 핸들러 생성 중...")
                self.device_handlers = []
                self.device_handler_map = {}
                
                for device_config in self.config['devices']:
                    try:
                        # DeviceFactory에 system_config 전달
                        handler = DeviceFactory.create_device(device_config, self.mqtt_client, system_config)
                        if handler is not None:
                            self.device_handlers.append(handler)
                            self.device_handler_map[device_config['name']] = handler
                            print(f"✅ 장비 핸들러 생성 성공: {device_config['name']} ({device_config['type']})")
                        else:
                            print(f"⚠️ 장비 핸들러 생성 실패 (비활성화): {device_config['name']} ({device_config['type']})")
                    except Exception as e:
                        print(f"  ❌ {device_config['name']} 생성 실패: {e}")
                
                # 데이터 매니저에 장비 핸들러 설정
                data_manager.set_device_handlers(self.device_handlers)
                
                # 운전 모드 관리자 초기화 (실시간 상태 전송을 위해 필수!)
                print("🎛️ 운전 모드 관리자 초기화 중...")
                
                # 현재 이벤트 루프를 OperationManager에 전달
                main_loop = asyncio.get_running_loop()
                self.operation_manager = OperationManager(
                    config=self.config,
                    device_handlers=self.device_handler_map,
                    mqtt_client=self.mqtt_client,
                    main_loop=main_loop  # 메인 루프 전달
                )
                await self.operation_manager.initialize()
                print("✅ 운전 모드 관리자 초기화 완료 - 실시간 상태 전송 활성화")
                
            else:
                raise ValueError("설정이 로드되지 않았습니다")
            
            # 스케줄러 초기화 및 작업 등록
            print("⏰ 스케줄러 초기화 중...")
            self.scheduler = PMSScheduler()
            for handler in self.device_handlers:
                self.scheduler.add_polling_job(handler)
                print(f"   📋 스케줄링 작업 등록: {handler.name}")
            
            # 스케줄러 시작
            print("▶️ 스케줄러 시작...")
            self.scheduler.start()
            print("✅ 스케줄러 시작 완료")
            
            self.server_running = True
            
            # 데이터 매니저 시스템 상태 업데이트
            data_manager.update_system_status(running=True)
            
            # 서버 상태 모니터링
            await self.monitor_server()
            
        except Exception as e:
            print(f"❌ PMS 서버 시작 실패: {e}")
            if self.logger:
                self.logger.error(f"서버 시작 실패: {e}")
            self.server_running = False
    
    async def monitor_server(self):
        """서버 상태 모니터링"""
        print("🔍 서버 상태 모니터링 시작")
        
        # main.py 스타일의 초기화 로그 출력
        print("🎉 === PMS 시스템 준비 완료 ===")
        print(f"📊 등록된 장비: {len(self.device_handlers)}개")
        auto_mode_enabled = self.config.get('auto_mode', {}).get('enabled', False) if self.config else False
        print(f"🤖 자동 운전 모드: {'활성화' if auto_mode_enabled else '비활성화'}")
        
        if self.operation_manager:
            # 제어 토픽 정보 출력
            control_topics = self.operation_manager.get_control_topics()
            print("📡 === MQTT 제어 토픽 ===")
            for topic_name, topic in control_topics.items():
                print(f"   📌 {topic_name}: {topic}")
        
        print("🔍 === 디버깅 정보 ===")
        print(f"📡 MQTT 연결 상태: {'연결됨' if self.mqtt_client and self.mqtt_client.is_connected() else '연결 안됨'}")
        if self.mqtt_client:
            print(f"📡 구독 토픽 수: {len(self.mqtt_client.get_subscribed_topics())}")
        
        print("⚠️ threshold_config 토픽이 수신되지 않으면 다음을 확인하세요:")
        print("   1. MQTT 브로커 설정이 백엔드와 일치하는지")
        print("   2. 토픽 이름이 정확한지 (pms/control/threshold_config)")
        print("   3. 네트워크 연결 상태")
        print("   4. 백엔드에서 실제로 메시지를 발행했는지")
        
        while self.server_running:
            try:
                # 주기적으로 서버 상태 출력
                await asyncio.sleep(30)  # 30초마다
                
                if self.mqtt_client and self.mqtt_client.is_connected():
                    status_msg = f"🟢 PMS 서버 정상 동작 중 (장비: {len(self.device_handlers)}개)"
                    print(f"[{time.strftime('%H:%M:%S')}] {status_msg}")
                else:
                    status_msg = "🟡 MQTT 연결 끊어짐 - 재연결 시도 중..."
                    print(f"[{time.strftime('%H:%M:%S')}] {status_msg}")
                    
            except Exception as e:
                error_msg = f"⚠️ 서버 모니터링 오류: {e}"
                print(error_msg)
                await asyncio.sleep(5)
    
    def start_server_thread(self):
        """별도 스레드에서 PMS 서버 실행"""
        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start_pms_server())
            except Exception as e:
                print(f"❌ 서버 스레드 오류: {e}")
            finally:
                loop.close()
        
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        print("🔄 PMS 서버가 백그라운드 스레드에서 시작되었습니다.")
    
    def stop_server(self):
        """PMS 서버 정지"""
        print("\n🛑 PMS 서버 정지 중...")
        self.server_running = False
        
        # 운전 모드 관리자 정지
        if self.operation_manager:
            try:
                # 운전 모드 관리자 종료는 별도 스레드에서 처리
                def shutdown_operation_manager():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        if self.operation_manager:
                            loop.run_until_complete(self.operation_manager.shutdown())
                    finally:
                        loop.close()
                
                shutdown_thread = threading.Thread(target=shutdown_operation_manager, daemon=True)
                shutdown_thread.start()
                shutdown_thread.join(timeout=5)  # 5초 대기
                print("✅ 운전 모드 관리자 정지 완료")
            except Exception as e:
                print(f"⚠️ 운전 모드 관리자 정지 중 오류: {e}")
        
        if self.scheduler:
            self.scheduler.shutdown()
            print("✅ 스케줄러 정지 완료")
        
        if self.mqtt_client:
            try:
                # MQTT 연결 해제는 별도 스레드에서 처리
                def disconnect_mqtt():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        if self.mqtt_client:
                            loop.run_until_complete(self.mqtt_client.disconnect())
                    finally:
                        loop.close()
                
                disconnect_thread = threading.Thread(target=disconnect_mqtt)
                disconnect_thread.start()
                disconnect_thread.join(timeout=3)
                print("✅ MQTT 연결 해제 완료")
            except Exception as e:
                print(f"⚠️ MQTT 연결 해제 중 오류: {e}")
        
        print("🔴 PMS 서버 정지 완료")
    
    def run(self):
        """통합 애플리케이션 실행"""
        print("=" * 60)
        print("🎯 PMS 통합 애플리케이션 시작")
        print("  - GUI 인터페이스: 데이터 모니터링 및 제어")
        print("  - 백그라운드 서버: Modbus 폴링 및 MQTT 발행")
        print("=" * 60)
        
        # 1. 설정 로드
        if not self.load_config():
            print("❌ 설정 로드 실패로 종료합니다.")
            return
        
        # 2. 백그라운드 PMS 서버 시작
        self.start_server_thread()
        time.sleep(2)  # 서버 시작 대기
        
        # 3. GUI 시작
        print("\n🖥️ GUI 인터페이스 시작 중...")
        try:
            if self.config:
                self.gui_app = PMSMainWindow(self.config)
            else:
                raise ValueError("설정이 없어서 GUI를 시작할 수 없습니다")
            
            # GUI 종료 시 서버도 함께 정지
            original_on_closing = self.gui_app.on_closing
            def integrated_on_closing():
                self.stop_server()
                original_on_closing()
            
            self.gui_app.on_closing = integrated_on_closing
            
            print("✅ GUI 창이 열렸습니다.")
            print("💡 GUI 창을 닫으면 전체 애플리케이션이 종료됩니다.")
            
            # GUI 실행 (블로킹)
            self.gui_app.run()
            
        except Exception as e:
            print(f"❌ GUI 실행 오류: {e}")
            self.stop_server()
        
        print("\n👋 통합 애플리케이션 종료")


def main():
    """메인 함수"""
    try:
        app = IntegratedPMSApp()
        app.run()
    except KeyboardInterrupt:
        print("\n⚠️ 사용자에 의해 중단됨")
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main() 