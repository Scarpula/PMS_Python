"""
PMS 메인 GUI 윈도우
탭 기반 장비별 모니터링 및 제어 인터페이스
"""

import tkinter as tk
from tkinter import ttk, messagebox
import asyncio
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
import json
import os
import time

# PMS 모듈 임포트
try:
    from ..devices import DeviceFactory
    from ..core.mqtt_client import MQTTClient
    
    # DataManager import 시도
    try:
        from ..core.data_manager import data_manager
        print("✅ data_manager import 성공")
    except (ImportError, AttributeError) as e:
        print(f"❌ data_manager import 실패: {e}")
        print("⚠️ 독립 모드로 실행 - 백그라운드 서버 연동 불가")
        data_manager = None
        
    # DBConfigLoader import 시도
    try:
        from ..core.db_config_loader import DBConfigLoader
    except (ImportError, AttributeError):
        DBConfigLoader = None
        
except ImportError:
    print("Warning: PMS 모듈을 import할 수 없습니다. 독립 실행 모드로 작동합니다.")
    DeviceFactory = None
    MQTTClient = None
    data_manager = None
    DBConfigLoader = None

from ..core.mqtt_client import MQTTClient
from ..devices import DeviceFactory

class PMSMainWindow:
    """PMS 메인 GUI 윈도우 클래스"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        GUI 초기화
        
        Args:
            config: 설정 딕셔너리
        """
        self.config = config
        self.root = tk.Tk()
        self.root.title("PMS 모니터링 및 제어 시스템")
        self.root.geometry("1400x900")  # 크기를 늘려서 우측 패널 공간 확보
        
        # 스타일 설정
        self.setup_styles()
        
        # 변수 초기화
        self.mqtt_client = None
        self.device_handlers = []
        self.device_tabs = {}
        self.running = False
        self.update_thread = None
        
        # DB 설정 로더 초기화
        self.db_config_loader = None
        if DBConfigLoader:
            try:
                db_config = self.config.get('database', {})
                if db_config.get('enabled', False):
                    db_url = db_config.get('url')
                    device_location = db_config.get('device_location')
                    
                    if not device_location:
                        print("⚠️ config.yml에 database.device_location이 설정되지 않았습니다")
                        return
                        
                    if db_url:
                        self.db_config_loader = DBConfigLoader(db_url, device_location)
                        print(f"✅ DB 설정 로더 초기화 완료 (위치: {device_location})")
                    else:
                        print("⚠️ DB URL이 설정되지 않았습니다")
            except Exception as e:
                print(f"⚠️ DB 설정 로더 초기화 실패: {e}")
        
        # 운전 모드 및 임계값 설정 변수들
        self.current_operation_mode = tk.StringVar(value="manual")
        self.soc_high_threshold = tk.DoubleVar(value=85.0)
        self.soc_low_threshold = tk.DoubleVar(value=50.0)
        self.soc_charge_stop_threshold = tk.DoubleVar(value=80.0)
        self.dcdc_standby_time = tk.IntVar(value=5)
        self.charging_power = tk.DoubleVar(value=30.0)
        
        # 통합 애플리케이션 모드 확인 (백그라운드 서버가 실행 중인지)
        self.integrated_mode = True  # 통합 애플리케이션으로 실행됨
        
        # GUI 구성 요소 생성
        self.create_widgets()
        
        # DB에서 초기 설정 로드
        self.load_initial_config()
        
        # 통합 모드에서는 바로 장비 탭 생성 (백그라운드 서버 사용)
        if self.integrated_mode:
            # 통합 모드에서도 loop 초기화 (오류 방지)
            self.loop = None
            # 통합 모드에서는 MQTT 클라이언트를 미리 연결하지 않음 (필요시에만 임시 연결)
            self.mqtt_client = None
            self.create_device_tabs_integrated()
            self.running = True
            self.update_ui_status()
            self.start_update_thread()
        else:
            # 비동기 이벤트 루프 설정 (독립 실행 모드에서만)
            self.loop = None
            self.setup_async_loop()
    
    def setup_styles(self):
        """GUI 스타일 설정"""
        style = ttk.Style()
        style.theme_use('clam')
        
        # 커스텀 스타일 정의
        style.configure('Header.TLabel', font=('Arial', 12, 'bold'))
        style.configure('Status.TLabel', font=('Arial', 10))
        style.configure('Connected.TLabel', foreground='green')
        style.configure('Disconnected.TLabel', foreground='red')
        style.configure('Control.TButton', font=('Arial', 10, 'bold'))
        style.configure('AutoMode.TButton', font=('Arial', 11, 'bold'), foreground='white')
        style.configure('ManualMode.TButton', font=('Arial', 11, 'bold'), foreground='white')
        
        # 운전 모드 버튼 색상 설정
        style.map('AutoMode.TButton', 
                  background=[('active', '#4CAF50'), ('!active', '#45a049')])
        style.map('ManualMode.TButton', 
                  background=[('active', '#2196F3'), ('!active', '#1976d2')])
    
    def create_widgets(self):
        """GUI 구성 요소 생성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        # 상단 제어 패널
        self.create_control_panel(main_frame)
        
        # 탭 노트북 (장비 모니터링) - 전체 영역 사용
        content_frame = ttk.Frame(main_frame)
        content_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        
        self.notebook = ttk.Notebook(content_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # 창 크기 조정 설정
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(0, weight=1)
    
    def create_control_panel(self, parent):
        """상단 제어 패널 생성"""
        control_frame = ttk.Frame(parent)
        control_frame.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 10))
        
        # 시스템 상태 표시
        ttk.Label(control_frame, text="시스템 상태:", style='Header.TLabel').grid(row=0, column=0, padx=(0, 10))
        
        self.status_label = ttk.Label(control_frame, text="중지됨", style='Disconnected.TLabel')
        self.status_label.grid(row=0, column=1, padx=(0, 20))
        
        # 통합 모드에서는 제어 버튼 비활성화 (백그라운드 서버가 관리)
        if self.integrated_mode:
            # 모니터링 전용 표시
            ttk.Label(control_frame, text="(백그라운드 서버 연동)", style='Status.TLabel').grid(row=0, column=2, padx=(0, 20))
            self.start_button = None
            self.stop_button = None
        else:
            # 제어 버튼들 (독립 실행 모드에서만)
            self.start_button = ttk.Button(control_frame, text="시작", command=self.start_system, style='Control.TButton')
            self.start_button.grid(row=0, column=2, padx=(0, 10))
            
            self.stop_button = ttk.Button(control_frame, text="정지", command=self.stop_system, style='Control.TButton', state='disabled')
            self.stop_button.grid(row=0, column=3, padx=(0, 10))
        
        # MQTT 연결 상태
        mqtt_col = 3 if self.integrated_mode else 4
        ttk.Label(control_frame, text="MQTT:", style='Header.TLabel').grid(row=0, column=mqtt_col, padx=(20, 5))
        
        self.mqtt_status_label = ttk.Label(control_frame, text="연결안됨", style='Disconnected.TLabel')
        self.mqtt_status_label.grid(row=0, column=mqtt_col+1)
    
    def create_operation_control_panel(self, parent):
        """우측 운전 모드 제어 패널 생성"""
        right_frame = ttk.LabelFrame(parent, text="🎛️ 운전 모드 제어", padding="15")
        right_frame.grid(row=0, column=1, sticky="nsew")
        
        # 현재 운전 모드 표시
        mode_display_frame = ttk.Frame(right_frame)
        mode_display_frame.pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(mode_display_frame, text="현재 모드:", font=('Arial', 11, 'bold')).pack(side=tk.LEFT)
        self.current_mode_label = ttk.Label(mode_display_frame, text="수동 모드", 
                                           font=('Arial', 11, 'bold'), foreground='blue')
        self.current_mode_label.pack(side=tk.LEFT, padx=(10, 0))
        
        # 운전 모드 버튼들
        mode_button_frame = ttk.Frame(right_frame)
        mode_button_frame.pack(fill=tk.X, pady=(0, 20))
        
        self.manual_mode_btn = ttk.Button(mode_button_frame, text="🔧 수동 운전 모드", 
                                         command=self.set_manual_mode, style='ManualMode.TButton')
        self.manual_mode_btn.pack(side=tk.LEFT, padx=(0, 10), ipady=8)
        
        self.auto_mode_btn = ttk.Button(mode_button_frame, text="🤖 자동 운전 모드", 
                                       command=self.set_auto_mode, style='AutoMode.TButton')
        self.auto_mode_btn.pack(side=tk.LEFT, ipady=8)
        
        # 구분선
        separator1 = ttk.Separator(right_frame, orient='horizontal')
        separator1.pack(fill=tk.X, pady=(0, 15))
        
        # 임계값 설정 영역
        threshold_label = ttk.Label(right_frame, text="⚙️ 자동 운전 임계값 설정", 
                                   font=('Arial', 11, 'bold'))
        threshold_label.pack(anchor=tk.W, pady=(0, 10))
        
        # 임계값 입력 필드들을 스크롤 가능한 프레임에 배치
        canvas = tk.Canvas(right_frame, height=280)
        scrollbar = ttk.Scrollbar(right_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # SOC 상한 임계값
        self.create_threshold_input(scrollable_frame, "SOC 상한 임계값:", self.soc_high_threshold, "%", 0)
        
        # SOC 하한 임계값  
        self.create_threshold_input(scrollable_frame, "SOC 하한 임계값:", self.soc_low_threshold, "%", 1)
        
        # SOC 충전 정지 임계값
        self.create_threshold_input(scrollable_frame, "SOC 충전 정지 임계값:", self.soc_charge_stop_threshold, "%", 2)
        
        # DCDC 대기 시간
        self.create_threshold_input(scrollable_frame, "DCDC 대기 시간:", self.dcdc_standby_time, "분", 3)
        
        # 충전 전력
        self.create_threshold_input(scrollable_frame, "충전 전력:", self.charging_power, "kW", 4)
        
        canvas.pack(side="left", fill="both", expand=True, pady=(0, 15))
        scrollbar.pack(side="right", fill="y", pady=(0, 15))
        
        # 구분선
        separator2 = ttk.Separator(right_frame, orient='horizontal')
        separator2.pack(fill=tk.X, pady=(0, 15))
        
        # 제어 버튼들
        control_button_frame = ttk.Frame(right_frame)
        control_button_frame.pack(fill=tk.X)
        
        # DB에서 불러오기 버튼
        load_btn = ttk.Button(control_button_frame, text="📥 DB에서 불러오기", 
                             command=self.load_config_from_db)
        load_btn.pack(side=tk.LEFT, padx=(0, 10), ipady=5)
        
        # DB에 저장 버튼
        save_btn = ttk.Button(control_button_frame, text="💾 DB에 저장", 
                             command=self.save_config_to_db, style='Control.TButton')
        save_btn.pack(side=tk.LEFT, ipady=5)
        
        # 자동 모드 시작/정지 버튼들
        auto_control_frame = ttk.Frame(right_frame)
        auto_control_frame.pack(fill=tk.X, pady=(15, 0))
        
        self.auto_start_btn = ttk.Button(auto_control_frame, text="🚀 자동 모드 시작", 
                                        command=self.start_auto_mode, style='AutoMode.TButton')
        self.auto_start_btn.pack(side=tk.LEFT, padx=(0, 10), ipady=5)
        
        self.auto_stop_btn = ttk.Button(auto_control_frame, text="🛑 자동 모드 정지", 
                                       command=self.stop_auto_mode, style='ManualMode.TButton')
        self.auto_stop_btn.pack(side=tk.LEFT, ipady=5)
    
    def create_threshold_input(self, parent, label_text, variable, unit, row):
        """임계값 입력 필드 생성"""
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="ew", pady=5)
        parent.grid_columnconfigure(0, weight=1)
        
        # 라벨
        label = ttk.Label(frame, text=label_text, width=18, anchor='w')
        label.grid(row=0, column=0, sticky="w")
        
        # 입력 필드
        entry = ttk.Entry(frame, textvariable=variable, width=10, justify='center')
        entry.grid(row=0, column=1, padx=(5, 5))
        
        # 단위
        unit_label = ttk.Label(frame, text=unit, width=4, anchor='w')
        unit_label.grid(row=0, column=2, sticky="w")
    
    def load_initial_config(self):
        """초기 설정 로드 (DB에서)"""
        if self.db_config_loader:
            try:
                # 비동기 함수를 동기적으로 실행
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                config = loop.run_until_complete(self.db_config_loader.load_auto_mode_config())
                if config:
                    self.soc_high_threshold.set(config.get('soc_high_threshold', 85.0))
                    self.soc_low_threshold.set(config.get('soc_low_threshold', 50.0))
                    self.soc_charge_stop_threshold.set(config.get('soc_charge_stop_threshold', 80.0))
                    self.dcdc_standby_time.set(config.get('dcdc_standby_time', 5))
                    self.charging_power.set(config.get('charging_power', 30.0))
                    
                    # 🔧 현재 운전 모드도 DB에서 로드하여 반영
                    auto_mode_enabled = config.get('auto_mode_enabled', False)
                    if auto_mode_enabled:
                        self.current_operation_mode.set("auto")
                    else:
                        self.current_operation_mode.set("manual")
                    
                    print("✅ DB에서 초기 설정 로드 완료")
                    print(f"   📊 로드된 운전 모드: {'자동' if auto_mode_enabled else '수동'}")
                else:
                    print("⚠️ DB에서 설정을 찾을 수 없음, 기본값 사용")
            except Exception as e:
                print(f"❌ 초기 설정 로드 중 오류: {e}")
    
    def load_config_from_db(self):
        """DB에서 설정 불러오기"""
        if not self.db_config_loader:
            messagebox.showwarning("경고", "DB 연결이 설정되지 않았습니다.")
            return
        
        try:
            def load_async():
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                if self.db_config_loader is not None:
                    config = loop.run_until_complete(self.db_config_loader.load_auto_mode_config())
                else:
                    config = None
                return config
            
            config = load_async()
            if config:
                self.soc_high_threshold.set(config.get('soc_high_threshold', 85.0))
                self.soc_low_threshold.set(config.get('soc_low_threshold', 50.0))
                self.soc_charge_stop_threshold.set(config.get('soc_charge_stop_threshold', 80.0))
                self.dcdc_standby_time.set(config.get('dcdc_standby_time', 5))
                self.charging_power.set(config.get('charging_power', 30.0))
                
                # 🔧 현재 운전 모드도 반영
                auto_mode_enabled = config.get('auto_mode_enabled', False)
                if auto_mode_enabled:
                    self.current_operation_mode.set("auto")
                else:
                    self.current_operation_mode.set("manual")
                
                messagebox.showinfo("성공", f"DB에서 설정을 성공적으로 불러왔습니다.\n운전 모드: {'자동' if auto_mode_enabled else '수동'}")
            else:
                messagebox.showwarning("경고", "DB에서 설정을 찾을 수 없습니다.")
                
        except Exception as e:
            messagebox.showerror("오류", f"DB에서 설정 불러오기 실패: {e}")
    
    def save_config_to_db(self):
        """DB에 설정 저장 및 MQTT로 전송"""
        if not self.db_config_loader:
            messagebox.showwarning("경고", "DB 연결이 설정되지 않았습니다.")
            return
        
        try:
            # 현재 GUI 값들 수집
            config_data = {
                'soc_high_threshold': self.soc_high_threshold.get(),
                'soc_low_threshold': self.soc_low_threshold.get(),
                'soc_charge_stop_threshold': self.soc_charge_stop_threshold.get(),
                'dcdc_standby_time': self.dcdc_standby_time.get(),
                'charging_power': self.charging_power.get()
            }
            
            # 입력값 검증
            if not self.validate_config_values(config_data):
                return
            
            # DB에 저장
            def save_async():
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                if self.db_config_loader is not None:
                    return loop.run_until_complete(self.db_config_loader.save_auto_mode_config(config_data))
                else:
                    return False
            
            success = save_async()
            
            if success:
                # MQTT로 임계값 설정 전송
                self.send_threshold_config_mqtt(config_data)
                messagebox.showinfo("성공", "설정이 DB에 저장되고 시스템에 적용되었습니다.")
            else:
                messagebox.showerror("오류", "DB 저장에 실패했습니다.")
                
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 오류: {e}")
    
    def validate_config_values(self, config_data):
        """설정값 검증"""
        try:
            # SOC 값들이 0-100 범위인지 확인
            for key in ['soc_high_threshold', 'soc_low_threshold', 'soc_charge_stop_threshold']:
                value = config_data[key]
                if not (0 <= value <= 100):
                    messagebox.showerror("입력 오류", f"{key}는 0-100 범위여야 합니다. (현재값: {value})")
                    return False
            
            # SOC 임계값 논리 확인
            if config_data['soc_low_threshold'] >= config_data['soc_high_threshold']:
                messagebox.showerror("입력 오류", "SOC 하한 임계값은 상한 임계값보다 작아야 합니다.")
                return False
            
            if config_data['soc_charge_stop_threshold'] > config_data['soc_high_threshold']:
                messagebox.showerror("입력 오류", "충전 정지 임계값은 상한 임계값보다 작거나 같아야 합니다.")
                return False
            
            # DCDC 대기 시간이 양수인지 확인
            if config_data['dcdc_standby_time'] <= 0:
                messagebox.showerror("입력 오류", "DCDC 대기 시간은 양수여야 합니다.")
                return False
            
            # 충전 전력이 양수인지 확인
            if config_data['charging_power'] <= 0:
                messagebox.showerror("입력 오류", "충전 전력은 양수여야 합니다.")
                return False
            
            return True
            
        except Exception as e:
            messagebox.showerror("검증 오류", f"설정값 검증 중 오류: {e}")
            return False
    
    def send_threshold_config_mqtt(self, config_data):
        """MQTT로 임계값 설정 전송"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.config.get('database', {}).get('device_location', 'Unknown')
            mqtt_message = {
                "command": "threshold_config",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "config": config_data,
                "source": "gui_control_panel"
            }
            
            # 임계값 설정 토픽
            threshold_topic = "pms/control/threshold_config"
            
            # 비동기 MQTT 전송
            def send_mqtt():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if hasattr(self, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.send_mqtt_control_command_temp(threshold_topic, mqtt_message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        print(f"✅ 임계값 설정 MQTT 전송 완료: {threshold_topic}")
                    else:
                        print(f"❌ 임계값 설정 MQTT 전송 실패")
                        
                except Exception as e:
                    print(f"❌ MQTT 전송 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_mqtt, daemon=True)
            thread.start()
            
        except Exception as e:
            print(f"❌ MQTT 메시지 구성 중 오류: {e}")
    
    def set_manual_mode(self):
        """수동 운전 모드 설정"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.config.get('database', {}).get('device_location', 'Unknown')
            message = {
                "mode": "basic",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_control_panel"
            }
            
            # 운전 모드 변경 토픽
            mode_topic = "pms/control/operation_mode"
            
            # 비동기 MQTT 전송
            def send_mode_change():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if hasattr(self, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.send_mqtt_control_command_temp(mode_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        self.current_operation_mode.set("manual")
                        self.current_mode_label.config(text="수동 모드", foreground='blue')
                        messagebox.showinfo("모드 변경", "수동 운전 모드로 변경되었습니다.")
                    else:
                        messagebox.showerror("오류", "수동 모드 설정 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"수동 모드 설정 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_mode_change, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"수동 모드 설정 실패: {e}")
    
    def set_auto_mode(self):
        """자동 운전 모드 설정"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.config.get('database', {}).get('device_location', 'Unknown')
            message = {
                "mode": "auto",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_control_panel"
            }
            
            # 운전 모드 변경 토픽
            mode_topic = "pms/control/operation_mode"
            
            # 비동기 MQTT 전송
            def send_mode_change():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if hasattr(self, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.send_mqtt_control_command_temp(mode_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        self.current_operation_mode.set("auto")
                        self.current_mode_label.config(text="자동 모드", foreground='green')
                        messagebox.showinfo("모드 변경", "자동 운전 모드로 변경되었습니다.")
                    else:
                        messagebox.showerror("오류", "자동 모드 설정 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"자동 모드 설정 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_mode_change, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"자동 모드 설정 실패: {e}")
    
    def start_auto_mode(self):
        """자동 모드 시작"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.config.get('database', {}).get('device_location', 'Unknown')
            message = {
                "command": "auto_start",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_control_panel"
            }
            
            # 자동 모드 시작 토픽
            start_topic = "pms/control/auto_mode/start"
            
            # 비동기 MQTT 전송
            def send_auto_start():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if hasattr(self, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.send_mqtt_control_command_temp(start_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        messagebox.showinfo("자동 모드", "자동 모드 시작 명령을 전송했습니다.")
                    else:
                        messagebox.showerror("오류", "자동 모드 시작 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"자동 모드 시작 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_auto_start, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"자동 모드 시작 실패: {e}")
    
    def stop_auto_mode(self):
        """자동 모드 정지"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.config.get('database', {}).get('device_location', 'Unknown')
            message = {
                "command": "auto_stop",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_control_panel"
            }
            
            # 자동 모드 정지 토픽
            stop_topic = "pms/control/auto_mode/stop"
            
            # 비동기 MQTT 전송
            def send_auto_stop():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if hasattr(self, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.send_mqtt_control_command_temp(stop_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        messagebox.showinfo("자동 모드", "자동 모드 정지 명령을 전송했습니다.")
                    else:
                        messagebox.showerror("오류", "자동 모드 정지 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"자동 모드 정지 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_auto_stop, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"자동 모드 정지 실패: {e}")
    
    async def send_mqtt_control_command_temp(self, topic: str, payload: dict) -> bool:
        """임시 MQTT 연결을 통한 제어 명령 전송"""
        temp_mqtt_client = None
        try:
            if MQTTClient is None:
                print("Warning: MQTTClient를 import할 수 없습니다.")
                return False
            
            # 임시 MQTT 클라이언트를 위한 설정 생성 (유니크한 client_id 사용)
            import time
            temp_config = self.config['mqtt'].copy()
            temp_config['client_id'] = f"pms_gui_temp_{int(time.time() * 1000)}"
            
            # 임시 MQTT 클라이언트 생성 및 연결
            temp_mqtt_client = MQTTClient(temp_config)
            await temp_mqtt_client.connect()
            
            if not temp_mqtt_client.is_connected():
                print("❌ 임시 MQTT 연결 실패")
                return False
            
            # 제어 명령 전송
            success = temp_mqtt_client.publish(topic, payload)
            if success:
                print(f"✅ 제어 명령 전송 성공: {topic}")
                return True
            else:
                print(f"❌ 제어 명령 전송 실패: {topic}")
                return False
                
        except Exception as e:
            print(f"❌ 임시 MQTT 제어 명령 전송 오류: {e}")
            return False
        finally:
            # 임시 연결 해제
            if temp_mqtt_client:
                try:
                    await temp_mqtt_client.disconnect()
                    print("🔌 임시 MQTT 연결 해제 완료")
                except:
                    pass
    
    def setup_async_loop(self):
        """비동기 이벤트 루프 설정"""
        def run_async_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
        
        self.async_thread = threading.Thread(target=run_async_loop, daemon=True)
        self.async_thread.start()
    
    def create_device_tabs(self):
        """장비별 탭 생성"""
        for device_config in self.config['devices']:
            device_type = device_config['type']
            device_name = device_config['name']
            
            # 탭 프레임 생성
            tab_frame = ttk.Frame(self.notebook)
            self.notebook.add(tab_frame, text=f"{device_type} - {device_name}")
            
            # 장비별 탭 클래스 생성
            if device_type == 'BMS':
                device_tab = BMSTab(tab_frame, device_config, self.device_handlers, self)
            elif device_type == 'DCDC':
                device_tab = DCDCTab(tab_frame, device_config, self.device_handlers, self)
            elif device_type == 'PCS':
                device_tab = PCSTab(tab_frame, device_config, self.device_handlers, self)
            else:
                continue
            
            self.device_tabs[device_name] = device_tab
    
    def create_device_tabs_integrated(self):
        """통합 모드용 장비별 탭 생성 (백그라운드 서버 사용)"""
        for device_config in self.config['devices']:
            device_type = device_config['type']
            device_name = device_config['name']
            
            # 탭 프레임 생성
            tab_frame = ttk.Frame(self.notebook)
            self.notebook.add(tab_frame, text=f"{device_type} - {device_name}")
            
            # 장비별 탭 클래스 생성 (모니터링 전용)
            if device_type == 'BMS':
                device_tab = BMSTab(tab_frame, device_config, [], self)  # 빈 핸들러 리스트
            elif device_type == 'DCDC':
                device_tab = DCDCTab(tab_frame, device_config, [], self)
            elif device_type == 'PCS':
                device_tab = PCSTab(tab_frame, device_config, [], self)
            else:
                continue
            
            # 통합 모드 플래그 설정
            device_tab.integrated_mode = True
            self.device_tabs[device_name] = device_tab
    
    def start_system(self):
        """시스템 시작"""
        if self.running:
            return
        
        try:
            # 비동기 작업 실행
            if self.loop is not None:
                future = asyncio.run_coroutine_threadsafe(self._start_system_async(), self.loop)
                future.result(timeout=10)  # 10초 타임아웃
            else:
                messagebox.showerror("오류", "비동기 루프가 초기화되지 않았습니다")
            
        except Exception as e:
            messagebox.showerror("오류", f"시스템 시작 실패: {e}")
    
    async def _start_system_async(self):
        """시스템 시작 (비동기)"""
        try:
            # DeviceFactory 존재 확인
            if DeviceFactory is None:
                raise ImportError("DeviceFactory를 import할 수 없습니다.")
            
            # MQTT 클라이언트 생성 및 연결
            if MQTTClient is None:
                raise ImportError("MQTTClient를 import할 수 없습니다.")
                
            self.mqtt_client = MQTTClient(self.config['mqtt'])
            await self.mqtt_client.connect()
            
            # 시스템 설정 생성
            system_config = {
                'simulation_mode': self.config.get('simulation_mode', False),
                'connection_timeout': self.config.get('connection_timeout', 5),
                'log_level': self.config.get('log_level', 'INFO')
            }
            
            # 장비 핸들러 생성
            self.device_handlers = []
            for device_config in self.config['devices']:
                handler = DeviceFactory.create_device(device_config, self.mqtt_client, system_config)
                if handler:
                    self.device_handlers.append(handler)
            
            # 탭 생성
            self.root.after(0, self.create_device_tabs)
            
            # 상태 업데이트
            self.running = True
            self.root.after(0, self.update_ui_status)
            
            # 데이터 업데이트 스레드 시작
            self.start_update_thread()
            
        except Exception as e:
            raise e
    
    def stop_system(self):
        """시스템 정지"""
        if not self.running:
            return
        
        self.running = False
        
        # 업데이트 스레드 정지
        if self.update_thread and self.update_thread.is_alive():
            self.update_thread.join(timeout=2)
        
        # MQTT 연결 해제
        if self.mqtt_client and self.loop is not None:
            future = asyncio.run_coroutine_threadsafe(self.mqtt_client.disconnect(), self.loop)
            try:
                future.result(timeout=5)
            except:
                pass
        
        # UI 상태 업데이트
        self.update_ui_status()
    
    def update_ui_status(self):
        """UI 상태 업데이트 (통합 모드)"""
        try:
            # 데이터 매니저가 있는 경우에만 시스템 상태 가져오기
            if data_manager is not None:
                system_status = data_manager.get_system_status()
                
                # 시스템 상태 라벨 업데이트
                if system_status.get('running', False):
                    self.status_label.config(text="시스템 상태: 실행중 (백그라운드 서버 연동)", style='Connected.TLabel')
                else:
                    self.status_label.config(text="시스템 상태: 정지됨", style='Disconnected.TLabel')
                
                # MQTT 상태 업데이트
                mqtt_status = system_status.get('mqtt_connected', False)
                if mqtt_status:
                    self.mqtt_status_label.config(text="MQTT: 연결됨", style='Connected.TLabel')
                else:
                    self.mqtt_status_label.config(text="MQTT: 연결안됨", style='Disconnected.TLabel')
            else:
                # 데이터 매니저가 없는 경우 (독립 모드)
                print("⚠️ data_manager가 None - 통합 모드 실행 필요")
                print("💡 해결 방법: python main_gui_integrated.py 실행")
                self.status_label.config(text="시스템 상태: 독립모드 (데이터 연결 안됨)", style='Disconnected.TLabel')
                self.mqtt_status_label.config(text="MQTT: 독립모드", style='Status.TLabel')
                
            # 각 장비 탭의 데이터 업데이트
            if hasattr(self, 'device_tabs'):
                for tab in self.device_tabs:
                    if hasattr(tab, 'update_data'):
                        try:
                            tab.update_data()
                        except Exception as e:
                            print(f"탭 {tab.__class__.__name__} 업데이트 오류: {e}")
                
        except Exception as e:
            print(f"UI 상태 업데이트 오류: {e}")
            self.status_label.config(text="시스템 상태: 오류", style='Disconnected.TLabel')
            self.mqtt_status_label.config(text="MQTT: 오류", style='Disconnected.TLabel')
    
    def start_update_thread(self):
        """데이터 업데이트 스레드 시작"""
        print(f"🔄 데이터 업데이트 스레드 시작 (통합모드: {self.integrated_mode})")
        print(f"   📊 data_manager 상태: {'연결됨' if data_manager is not None else 'None'}")
        print(f"   📱 장비 탭 수: {len(self.device_tabs) if hasattr(self, 'device_tabs') else 0}")
        
        def update_loop():
            while self.running:
                try:
                    # 각 탭의 데이터 업데이트
                    for tab in self.device_tabs.values():
                        if hasattr(tab, 'update_data'):
                            self.root.after(0, tab.update_data)
                    
                    # 1초마다 업데이트
                    threading.Event().wait(1)
                    
                except Exception as e:
                    print(f"업데이트 오류: {e}")
        
        self.update_thread = threading.Thread(target=update_loop, daemon=True)
        self.update_thread.start()
    
    def run(self):
        """GUI 실행"""
        try:
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
            self.root.mainloop()
        finally:
            self.cleanup()
    
    def on_closing(self):
        """창 닫기 이벤트 처리"""
        if self.running:
            self.stop_system()
        
        self.cleanup()
        self.root.destroy()
    
    def cleanup(self):
        """리소스 정리"""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


class DeviceTab:
    """장비 탭 기본 클래스"""
    
    def __init__(self, parent, device_config: Dict[str, Any], handlers: List, main_window=None):
        self.parent = parent
        self.device_config = device_config
        self.handlers = handlers
        self.device_name = device_config['name']
        self.device_type = device_config['type']
        self.integrated_mode = False  # 통합 모드 플래그 추가
        self.main_window = main_window  # 메인 윈도우 참조 저장
        
        # 핸들러 찾기
        self.device_handler = None
        for handler in handlers:
            if handler.name == self.device_name:
                self.device_handler = handler
                break
        
        self.create_widgets()
    
    def create_widgets(self):
        """위젯 생성 (하위 클래스에서 구현)"""
        pass
    
    def update_data(self):
        """데이터 업데이트 (하위 클래스에서 구현)"""
        pass
    
    def update_data_display(self, device_data):
        """데이터 표시 영역 업데이트 (하위 클래스에서 구현)"""
        pass
    
    def create_scrollable_treeview(self, parent, columns):
        """스크롤 가능한 트리뷰 생성 (공통 메소드)"""
        # 트리뷰 프레임
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        # 트리뷰 생성
        tree = ttk.Treeview(tree_frame, columns=columns, show='headings')
        
        # 수직 스크롤바
        v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=v_scrollbar.set)
        
        # 수평 스크롤바
        h_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=h_scrollbar.set)
        
        # 그리드 배치
        tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        # 프레임 크기 조정 설정
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        # 우클릭 컨텍스트 메뉴 추가
        self.create_context_menu(tree, columns)
        
        return tree
    
    def create_scrollable_control_frame(self, parent, text="제어"):
        """스크롤 가능한 제어 프레임 생성 (공통 메소드)"""
        # 외부 라벨프레임
        control_labelframe = ttk.LabelFrame(parent, text=text, padding="10")
        control_labelframe.pack(fill=tk.X)
        
        # 캔버스와 스크롤바를 위한 프레임
        canvas_frame = ttk.Frame(control_labelframe)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        # 캔버스 생성
        canvas = tk.Canvas(canvas_frame, height=150)  # 고정 높이 설정
        
        # 수평 스크롤바
        h_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(xscrollcommand=h_scrollbar.set)
        
        # 실제 내용이 들어갈 프레임
        scrollable_frame = ttk.Frame(canvas)
        
        # 캔버스 내부에 프레임 배치
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        
        # 스크롤 영역 업데이트를 위한 바인딩
        def configure_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # 캔버스 높이를 내용에 맞게 조정 (최대 150px)
            canvas_height = min(scrollable_frame.winfo_reqheight(), 150)
            canvas.configure(height=canvas_height)
        
        scrollable_frame.bind("<Configure>", configure_scroll_region)
        
        # 마우스 휠 스크롤 지원
        def on_mousewheel(event):
            # Shift 키 또는 그냥 휠로 수평 스크롤
            canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        # 그리드 배치
        canvas.grid(row=0, column=0, sticky="ew")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        # 크기 조정 설정
        canvas_frame.grid_columnconfigure(0, weight=1)
        
        return scrollable_frame
    
    def create_context_menu(self, tree, columns):
        """TreeView용 우클릭 컨텍스트 메뉴 생성"""
        # 컨텍스트 메뉴 생성
        context_menu = tk.Menu(tree, tearoff=0)
        
        # 메뉴 항목들
        context_menu.add_command(label="📋 전체 행 복사", command=lambda: self.copy_full_row(tree, columns))
        context_menu.add_command(label="📋 주소만 복사", command=lambda: self.copy_cell_value(tree, 'address'))
        context_menu.add_command(label="📋 값만 복사", command=lambda: self.copy_cell_value(tree, 'value'))
        context_menu.add_separator()
        context_menu.add_command(label="📋 주소:값 형태로 복사", command=lambda: self.copy_address_value_pair(tree))
        context_menu.add_command(label="📋 HEX 변환 정보 복사", command=lambda: self.copy_hex_info(tree))
        
        def show_context_menu(event):
            """우클릭 시 컨텍스트 메뉴 표시"""
            # 클릭한 아이템 선택
            item = tree.identify_row(event.y)
            if item:
                tree.selection_set(item)
                context_menu.post(event.x_root, event.y_root)
        
        # 우클릭 이벤트 바인딩
        tree.bind("<Button-3>", show_context_menu)
    
    def copy_to_clipboard(self, widget, text):
        """안정적인 클립보드 복사"""
        try:
            # tkinter 클립보드 방법 1
            widget.clipboard_clear()
            widget.clipboard_append(text)
            widget.update()  # 중요: update() 호출로 클립보드 적용
            
            # 추가 검증: 복사된 내용 확인
            try:
                copied_text = widget.selection_get(selection="CLIPBOARD")
                if copied_text == text:
                    return True
            except:
                pass
                
            # 방법 2: 다른 방식으로 시도
            try:
                import subprocess
                import sys
                if sys.platform == "win32":
                    # Windows cmd 사용
                    subprocess.run(['cmd', '/c', f'echo {text} | clip'], shell=True, check=True)
                    return True
            except:
                pass
                
            return False
            
        except Exception as e:
            print(f"❌ 클립보드 복사 실패: {e}")
            return False
    
    def copy_full_row(self, tree, columns):
        """선택된 행의 전체 데이터를 복사"""
        try:
            selected_item = tree.selection()[0]
            values = tree.item(selected_item, 'values')
            
            # 컬럼명:값 형태로 구성
            row_data = []
            for i, col in enumerate(columns):
                if i < len(values):
                    row_data.append(f"{col}: {values[i]}")
            
            clipboard_text = " | ".join(row_data)
            self.copy_to_clipboard(tree, clipboard_text)
            print(f"📋 전체 행 복사됨: {clipboard_text}")
            
        except IndexError:
            print("⚠️ 선택된 행이 없습니다.")
        except Exception as e:
            print(f"❌ 복사 중 오류: {e}")
    
    def copy_cell_value(self, tree, column_name):
        """선택된 행의 특정 컬럼 값을 복사"""
        try:
            selected_item = tree.selection()[0]
            values = tree.item(selected_item, 'values')
            
            # 컬럼 인덱스 찾기
            column_index = None
            for col in tree['columns']:
                if col == column_name:
                    column_index = tree['columns'].index(col)
                    break
            
            if column_index is not None and column_index < len(values):
                value = str(values[column_index])
                success = self.copy_to_clipboard(tree, value)
                if success:
                    print(f"📋 {column_name} 값 복사됨: {value}")
                else:
                    print(f"❌ {column_name} 값 복사 실패: {value}")
            else:
                print(f"⚠️ {column_name} 컬럼을 찾을 수 없습니다.")
                
        except IndexError:
            print("⚠️ 선택된 행이 없습니다.")
        except Exception as e:
            print(f"❌ 복사 중 오류: {e}")
    
    def copy_address_value_pair(self, tree):
        """주소:값 형태로 복사"""
        try:
            selected_item = tree.selection()[0]
            values = tree.item(selected_item, 'values')
            
            # address와 value 컬럼 찾기
            columns = tree['columns']
            address_idx = columns.index('address') if 'address' in columns else None
            value_idx = columns.index('value') if 'value' in columns else None
            
            if address_idx is not None and value_idx is not None:
                address = values[address_idx] if address_idx < len(values) else "N/A"
                value = values[value_idx] if value_idx < len(values) else "N/A"
                
                clipboard_text = f"Address:{address} = Value:{value}"
                success = self.copy_to_clipboard(tree, clipboard_text)
                if success:
                    print(f"📋 주소:값 쌍 복사됨: {clipboard_text}")
                else:
                    print(f"❌ 주소:값 쌍 복사 실패: {clipboard_text}")
            else:
                print("⚠️ 주소 또는 값 컬럼을 찾을 수 없습니다.")
                
        except IndexError:
            print("⚠️ 선택된 행이 없습니다.")
        except Exception as e:
            print(f"❌ 복사 중 오류: {e}")
    
    def copy_hex_info(self, tree):
        """HEX 변환 정보를 포함해서 복사 (비트마스크 데이터 특별 처리)"""
        try:
            selected_item = tree.selection()[0]
            values = tree.item(selected_item, 'values')
            
            columns = tree['columns']
            address_idx = columns.index('address') if 'address' in columns else None
            value_idx = columns.index('value') if 'value' in columns else None
            param_idx = columns.index('parameter') if 'parameter' in columns else None
            
            if address_idx is not None and value_idx is not None:
                address = values[address_idx] if address_idx < len(values) else "N/A"
                value_str = values[value_idx] if value_idx < len(values) else "N/A"
                parameter = values[param_idx] if param_idx is not None and param_idx < len(values) else "N/A"
                
                # 비트마스크 데이터 특별 처리
                hex_info = ""
                try:
                    # 비트마스크 형태인지 확인 (활성비트: 형태 포함)
                    if "활성비트:" in str(value_str):
                        # "1000 (활성비트:3) [Bit 3, Bit 5, Bit 6...]" 형태에서 숫자 추출
                        import re
                        match = re.match(r'^(\d+)', str(value_str))
                        if match:
                            decimal_val = int(match.group(1))
                            hex_val = f"0x{decimal_val:04X}"
                            binary_val = f"{decimal_val:016b}"
                            
                            # 활성 비트 정보 추출
                            active_match = re.search(r'활성비트:(\d+)', str(value_str))
                            active_count = active_match.group(1) if active_match else "0"
                            
                            hex_info = f" | RAW_DECIMAL:{decimal_val} | HEX:{hex_val} | Binary:{binary_val} | ActiveBits:{active_count}"
                        else:
                            hex_info = f" | BitMask_Data:{value_str}"
                    
                    # 일반 숫자 값 처리
                    elif str(value_str).replace(' (정상)', '').isdigit():
                        decimal_val = int(str(value_str).replace(' (정상)', ''))
                        hex_val = f"0x{decimal_val:04X}"
                        binary_val = f"{decimal_val:016b}"
                        hex_info = f" | HEX:{hex_val} | Binary:{binary_val}"
                    
                    # 주소 정보 추가
                    if str(address).replace('0x', '').isalnum():
                        if address.startswith('0x'):
                            hex_info += f" | Address:{address}"
                        elif str(address).isdigit():
                            decimal_addr = int(address)
                            hex_addr = f"0x{decimal_addr:04X}"
                            hex_info += f" | Address_DEC:{address} | Address_HEX:{hex_addr}"
                except Exception as parse_error:
                    hex_info = f" | ParseError:{parse_error}"
                
                clipboard_text = f"Parameter:{parameter} | Address:{address} | Value:{value_str}{hex_info}"
                success = self.copy_to_clipboard(tree, clipboard_text)
                if success:
                    print(f"📋 HEX 정보 포함 복사됨: {clipboard_text}")
                else:
                    print(f"❌ HEX 정보 복사 실패: {clipboard_text}")
            else:
                print("⚠️ 필요한 컬럼을 찾을 수 없습니다.")
                
        except IndexError:
            print("⚠️ 선택된 행이 없습니다.")
        except Exception as e:
            print(f"❌ 복사 중 오류: {e}")


class BMSTab(DeviceTab):
    """BMS 탭 클래스"""
    
    def create_widgets(self):
        """BMS 탭 위젯 생성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.parent, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 상단 정보 패널
        info_frame = ttk.LabelFrame(main_frame, text="장비 정보", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text=f"이름: {self.device_name}").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text=f"IP: {self.device_config['ip']}").grid(row=0, column=1, padx=(20, 0), sticky=tk.W)
        
        self.connection_label = ttk.Label(info_frame, text="연결 상태: 확인중", style='Status.TLabel')
        self.connection_label.grid(row=0, column=2, padx=(20, 0), sticky=tk.W)
        
        # 데이터 표시 영역
        data_frame = ttk.LabelFrame(main_frame, text="실시간 데이터", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 스크롤 가능한 데이터 트리뷰
        columns = ('address', 'parameter', 'value', 'unit', 'description')
        self.data_tree = self.create_scrollable_treeview(data_frame, columns)
        
        # 컬럼 설정
        self.data_tree.heading('address', text='주소')
        self.data_tree.heading('parameter', text='파라미터')
        self.data_tree.heading('value', text='값')
        self.data_tree.heading('unit', text='단위')
        self.data_tree.heading('description', text='설명')
        
        self.data_tree.column('address', width=80)
        self.data_tree.column('parameter', width=200)
        self.data_tree.column('value', width=150)
        self.data_tree.column('unit', width=80)
        self.data_tree.column('description', width=400)
        
        # 스크롤 가능한 제어 패널
        control_frame = self.create_scrollable_control_frame(main_frame, "BMS 제어")
        
        # 첫 번째 행: 수동 제어 버튼들
        ttk.Button(control_frame, text="데이터 읽기", command=self.read_data).grid(row=0, column=0, padx=(0, 10), pady=5)
        
        # BMS 전용 제어 버튼들
        ttk.Button(control_frame, text="DC 컨택터 ON", command=self.dc_contactor_on, style='Success.TButton').grid(row=0, column=1, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="DC 컨택터 OFF", command=self.dc_contactor_off, style='Danger.TButton').grid(row=0, column=2, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="에러 리셋", command=self.error_reset, style='Warning.TButton').grid(row=0, column=3, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="시스템 락 리셋", command=self.system_lock_reset, style='Warning.TButton').grid(row=0, column=4, padx=(5, 10), pady=5)
        
        # 두 번째 행: Write 파라미터 입력
        ttk.Label(control_frame, text="Write 주소:").grid(row=1, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.write_address_entry = ttk.Entry(control_frame, width=10)
        self.write_address_entry.grid(row=1, column=1, padx=(0, 5), pady=5)
        
        ttk.Label(control_frame, text="값:").grid(row=1, column=2, padx=(5, 5), pady=5, sticky=tk.W)
        self.write_value_entry = ttk.Entry(control_frame, width=10)
        self.write_value_entry.grid(row=1, column=3, padx=(0, 10), pady=5)
        
        ttk.Button(control_frame, text="Write", command=self.write_parameter).grid(row=1, column=4, pady=5)
        
        # 세 번째 행: IP 설정
        ttk.Label(control_frame, text="IP 설정 (A.B.C.D):").grid(row=2, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.ip_entry = ttk.Entry(control_frame, width=15)
        self.ip_entry.grid(row=2, column=1, columnspan=2, padx=(0, 5), pady=5, sticky=tk.W)
        self.ip_entry.insert(0, "192.168.1.60")  # 기본값
        
        ttk.Button(control_frame, text="IP 설정", command=self.set_ip_address).grid(row=2, column=3, padx=(5, 0), pady=5)
    
    def dc_contactor_on(self):
        """DC 접촉기 ON"""
        self.write_modbus_register(200, 1, "DC 접촉기 ON")
    
    def dc_contactor_off(self):
        """DC 접촉기 OFF"""
        self.write_modbus_register(200, 0, "DC 접촉기 OFF")
    
    def error_reset(self):
        """에러 리셋"""
        self.write_modbus_register(201, 80, "에러 리셋")
    
    def system_lock_reset(self):
        """시스템 락 리셋"""
        self.write_modbus_register(202, 80, "시스템 락 리셋")
    
    # 핸들러 편의 함수 직접 호출 메소드들 추가
    def bms_dc_contactor_control(self, state: bool):
        """BMS DC 접촉기 제어 (MQTT를 통한 백그라운드 서버 제어)"""
        try:
            # DC 접촉기 제어는 주소 200 사용
            value = 1 if state else 0
            description = f"DC 접촉기 {'ON' if state else 'OFF'}"
            self.write_modbus_register(200, value, description)
        except Exception as e:
            messagebox.showerror("오류", f"DC 접촉기 제어 중 오류: {e}")
    
    def bms_error_reset(self):
        """BMS 에러 리셋 (MQTT를 통한 백그라운드 서버 제어)"""
        try:
            # 에러 리셋은 주소 201, 값 80 사용
            self.write_modbus_register(201, 80, "BMS 에러 리셋")
        except Exception as e:
            messagebox.showerror("오류", f"에러 리셋 중 오류: {e}")
    
    def _subscribe_to_control_response(self, response_topic: str, request_id: str):
        """제어 명령 응답 구독"""
        try:
            mqtt_client = self.parent.master.mqtt_client
            if mqtt_client:
                # 응답 콜백 등록
                def on_control_response(topic, payload):
                    try:
                        response_data = json.loads(payload)
                        if response_data.get("request_id") == request_id:
                            success = response_data.get("success", False)
                            message = response_data.get("message", "")
                            
                            if success:
                                messagebox.showinfo("제어 성공", f"명령이 성공적으로 실행되었습니다.\n{message}")
                            else:
                                messagebox.showerror("제어 실패", f"명령 실행에 실패했습니다.\n{message}")
                                
                            # 일회성 구독 해제
                            mqtt_client.unsubscribe(response_topic)
                    except Exception as e:
                        print(f"제어 응답 처리 오류: {e}")
                
                # 임시 응답 구독 (5초 후 자동 해제)
                mqtt_client.subscribe(response_topic, on_control_response)
                
                # 5초 후 구독 해제 스케줄링
                def unsubscribe_after_timeout():
                    time.sleep(5)
                    try:
                        mqtt_client.unsubscribe(response_topic)
                    except:
                        pass
                
                import threading
                threading.Thread(target=unsubscribe_after_timeout, daemon=True).start()
                
        except Exception as e:
            print(f"제어 응답 구독 오류: {e}")

    def set_ip_address(self):
        """IP 주소 설정"""
        ip_str = self.ip_entry.get().strip()
        if not ip_str:
            messagebox.showwarning("경고", "IP 주소를 입력해주세요")
            return
        
        try:
            # IP 주소 파싱 (A.B.C.D)
            parts = ip_str.split('.')
            if len(parts) != 4:
                raise ValueError("잘못된 IP 형식")
            
            a, b, c, d = [int(x) for x in parts]
            if not all(0 <= x <= 255 for x in [a, b, c, d]):
                raise ValueError("IP 주소 범위 초과")
            
            # A.B와 C.D로 분리하여 16비트 값으로 변환
            ab_value = (a << 8) | b
            cd_value = (c << 8) | d
            
            result = messagebox.askyesno("확인", f"IP 주소를 {ip_str}로 설정하시겠습니까?\n(설정 후 장비가 재시작됩니다)")
            if result:
                self.write_modbus_register(203, ab_value, f"IP A.B 설정 (0x{ab_value:04X})")
                self.write_modbus_register(204, cd_value, f"IP C.D 설정 (0x{cd_value:04X})")
                self.write_modbus_register(205, 0xAA55, "RBMS 재시작")
                messagebox.showinfo("정보", f"IP 주소 설정 완료: {ip_str}\n장비가 재시작됩니다.")
                
        except ValueError as e:
            messagebox.showerror("오류", f"IP 주소 형식이 잘못되었습니다: {e}")

    def write_modbus_register(self, address, value, description):
        """Modbus 레지스터 쓰기 - 임시 MQTT 연결을 통한 백그라운드 서버 제어"""
        try:
            # 통합 모드에서는 임시 MQTT 연결을 통해 백그라운드 서버에 제어 명령 전송
            if self.integrated_mode and self.main_window:
                # 제어 명령 페이로드 생성
                command_data = {
                    "action": "write_register",
                    "address": address,
                    "value": value,
                    "description": description,
                    "timestamp": datetime.now().isoformat(),
                    "gui_request_id": f"{self.device_name}_{address}_{int(time.time() * 1000000)}"
                }
                
                # 임시 MQTT 연결을 통한 제어 명령 전송
                control_topic = f"pms/control/{self.device_name}/command"
                
                # 비동기 임시 MQTT 전송 실행
                def send_command():
                    import asyncio
                    try:
                        # 새 이벤트 루프에서 실행
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                            success = loop.run_until_complete(
                                self.main_window.send_mqtt_control_command_temp(control_topic, command_data)
                            )
                        else:
                            success = False
                        loop.close()
                        
                        if success:
                            messagebox.showinfo("제어 명령", f"{description} 명령을 백그라운드 서버로 전송했습니다.\n주소: {address}, 값: 0x{value:04X}")
                        else:
                            messagebox.showerror("오류", "MQTT 제어 명령 전송에 실패했습니다.")
                    except Exception as e:
                        messagebox.showerror("오류", f"제어 명령 전송 중 오류: {e}")
                
                # 별도 스레드에서 실행 (GUI 블로킹 방지)
                import threading
                thread = threading.Thread(target=send_command, daemon=True)
                thread.start()
                
            else:
                # 독립 모드에서는 직접 핸들러 접근 (기존 방식)
                if self.device_handler and hasattr(self.device_handler, 'write_register'):
                    self._execute_async_write(self.device_handler, address, value, description)
                else:
                    messagebox.showinfo("독립모드", f"{description} 명령 전송 (시뮬레이션)\n주소: {address}, 값: 0x{value:04X}")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")
    
    def _execute_async_write(self, handler, address, value, description):
        """비동기 쓰기 작업 실행"""
        try:
            # 메인 루프에서 실행되는 비동기 작업
            main_window = self.parent.master
            if hasattr(main_window, 'loop') and main_window.loop:
                # 레지스터 이름 찾기 (주소 -> 레지스터 이름 매핑)
                register_name = self._find_register_name_by_address(address)
                if register_name:
                    # 비동기 쓰기 작업 스케줄링
                    future = asyncio.run_coroutine_threadsafe(
                        handler.write_register(register_name, value), 
                        main_window.loop
                    )
                    # 결과 확인 (타임아웃 설정)
                    result = future.result(timeout=5)
                    if result:
                        messagebox.showinfo("성공", f"{description} 명령이 성공적으로 전송되었습니다.\n주소: {address}, 값: {value}")
                    else:
                        messagebox.showerror("실패", f"{description} 명령 전송에 실패했습니다.")
                else:
                    messagebox.showerror("오류", f"주소 {address}에 해당하는 레지스터를 찾을 수 없습니다.")
            else:
                messagebox.showwarning("경고", "비동기 루프가 실행되지 않았습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"비동기 쓰기 실행 중 오류: {e}")
    
    def _find_register_name_by_address(self, address):
        """주소로부터 레지스터 이름 찾기"""
        try:
            # BMS 메모리 맵에서 주소로 레지스터 이름 찾기
            memory_map = self._get_bms_memory_map()
            
            # 제어 레지스터에서 검색
            control_registers = memory_map.get('control_registers', {})
            for register_name, register_info in control_registers.items():
                if register_info.get('address') == address:
                    return register_name
            
            # 다른 섹션에서도 검색
            sections = ['data_registers', 'module_voltages', 'status_registers', 
                       'module_status_registers', 'module_temperatures', 'cell_voltages']
            
            for section in sections:
                section_data = memory_map.get(section, {})
                for register_name, register_info in section_data.items():
                    if register_info.get('address') == address:
                        return register_name
            
            return None
        except Exception as e:
            print(f"레지스터 이름 검색 오류: {e}")
            return None

    def update_data(self):
        """BMS 데이터 업데이트"""
        # 통합 모드에서는 데이터 매니저에서 데이터 가져오기
        if hasattr(self, 'integrated_mode') and self.integrated_mode and data_manager is not None:
            device_status = data_manager.get_device_status(self.device_name)
            device_data = data_manager.get_device_data(self.device_name)
            
            # 연결 상태 업데이트
            if device_status:
                if device_status.get('connected', False):
                    last_read = device_status.get('last_successful_read')
                    if last_read:
                        self.connection_label.config(text=f"연결 상태: 연결됨 (마지막: {last_read.strftime('%H:%M:%S') if hasattr(last_read, 'strftime') else str(last_read)})", style='Connected.TLabel')
                    else:
                        self.connection_label.config(text="연결 상태: 연결됨", style='Connected.TLabel')
                else:
                    error_msg = device_status.get('last_error', '연결안됨')
                    self.connection_label.config(text=f"연결 상태: {error_msg}", style='Disconnected.TLabel')
            else:
                self.connection_label.config(text="연결 상태: 확인중", style='Status.TLabel')
            
            # 실시간 데이터 표시
            self.update_data_display(device_data)
        else:
            # 통합 모드가 아니거나 data_manager가 None인 경우 디버깅 정보 출력
            if hasattr(self, 'integrated_mode') and self.integrated_mode and data_manager is None:
                print(f"⚠️ {self.device_name} BMS 탭: data_manager가 None - 통합 모드 실행 필요")
                self.connection_label.config(text="연결 상태: data_manager 없음", style='Disconnected.TLabel')
                return
            
            # 기존 로직 (독립 모드)
            if not self.device_handler:
                if hasattr(self, 'connection_label'):
                    self.connection_label.config(text="연결 상태: 핸들러 없음", style='Disconnected.TLabel')
                return
            
            try:
                # 연결 상태 업데이트
                if hasattr(self, 'connection_label'):
                    if self.device_handler and hasattr(self.device_handler, 'connected') and self.device_handler.connected:
                        self.connection_label.config(text="연결 상태: 연결됨", style='Connected.TLabel')
                    else:
                        self.connection_label.config(text="연결 상태: 연결안됨", style='Disconnected.TLabel')
                
                # 실제 데이터 읽기 시도
                self.update_real_data()
                
            except Exception as e:
                print(f"BMS 데이터 업데이트 오류: {e}")
                if hasattr(self, 'connection_label'):
                    self.connection_label.config(text="연결 상태: 오류", style='Disconnected.TLabel')
    
    def update_data_display(self, device_data):
        """데이터 표시 영역 업데이트"""
        # 기존 데이터 클리어
        for item in self.data_tree.get_children():
            self.data_tree.delete(item)
        
        if device_data:
            try:
                # 데이터 신선도 확인
                timestamp = device_data.get('timestamp')
                if timestamp:
                    if isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        except:
                            timestamp = datetime.now()
                    
                    age_seconds = (datetime.now() - timestamp).total_seconds()
                    if age_seconds > 300:  # 5분 초과
                        self.data_tree.insert('', tk.END, values=(
                            '-', 'status', '데이터 오래됨', '', f'{age_seconds:.0f}초 전 데이터'
                        ))
                        return
                
                # 실제 데이터 표시
                data = device_data.get('data', {})
                
                # 장비 정보 표시
                self.data_tree.insert('', tk.END, values=(
                    '-', 'device_name', data.get('device_name', 'N/A'), '', '장비 이름'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'device_type', data.get('device_type', 'N/A'), '', '장비 타입'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'ip_address', data.get('ip_address', 'N/A'), '', 'IP 주소'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'timestamp', timestamp.strftime('%H:%M:%S') if timestamp else 'N/A', '', '업데이트 시간'
                ))
                
                # 실제 센서 데이터가 있다면 표시
                sensor_data = data.get('data', {})
                if sensor_data:
                    # BMS 메모리 맵 정보 가져오기 시도
                    memory_map = self._get_bms_memory_map()
                    
                    for key, value in sensor_data.items():
                        # 메모리 맵에서 주소와 단위 정보 찾기
                        addr_info = self._find_address_info(key, memory_map)
                        address = addr_info.get('address', '-')
                        unit = addr_info.get('unit', '')
                        description = addr_info.get('description', '센서 데이터')
                        
                        # 16진수 주소 표시 (예: 0x0000)
                        addr_display = f"0x{address:04X}" if isinstance(address, int) else str(address)
                        
                        # 🔧 비트마스크 데이터 특별 처리
                        if isinstance(value, dict) and value.get('type') == 'bitmask':
                            # 비트마스크 데이터는 특별한 형태로 표시
                            raw_value = value.get('value', 0)
                            active_bits = value.get('active_bits', [])
                            total_active = len(active_bits)
                            
                            if total_active > 0:
                                # 활성 비트가 있으면 상세 정보 표시
                                display_value = f"{raw_value} (활성비트:{total_active}) [{', '.join([bit.split(':')[0] for bit in active_bits[:3]])}{'...' if total_active > 3 else ''}]"
                                description = f"{description} | {value.get('interpretation', '')}"
                            else:
                                # 활성 비트가 없으면 단순 표시
                                display_value = f"{raw_value} (정상)"
                                

                        else:
                            # 일반 데이터는 기존 방식
                            display_value = str(value)
                        
                        self.data_tree.insert('', tk.END, values=(
                            addr_display, key, display_value, unit, description
                        ))
                else:
                    self.data_tree.insert('', tk.END, values=(
                        '-', 'info', '센서 데이터 로드 중', '', '잠시 기다려주세요'
                    ))
                    
            except Exception as e:
                self.data_tree.insert('', tk.END, values=(
                    '-', 'error', '데이터 파싱 오류', '', str(e)
                ))
        else:
            self.data_tree.insert('', tk.END, values=(
                '-', 'status', '데이터 없음', '', '장비에서 데이터를 읽어오는 중입니다'
            ))
    
    def _get_bms_memory_map(self):
        """BMS 메모리 맵 가져오기"""
        try:
            import json
            import os
            
            # BMS 맵 파일 경로
            config_dir = os.path.join(os.path.dirname(__file__), '../../config')
            bms_map_path = os.path.join(config_dir, 'bms_map.json')
            
            if os.path.exists(bms_map_path):
                with open(bms_map_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                print(f"BMS 맵 파일을 찾을 수 없습니다: {bms_map_path}")
                return {}
        except Exception as e:
            print(f"BMS 메모리 맵 로드 오류: {e}")
            return {}
    
    def _find_address_info(self, data_key, memory_map):
        """데이터 키에 해당하는 주소 정보 찾기"""
        try:
            # 모든 섹션에서 검색
            sections = ['data_registers', 'module_voltages', 'status_registers', 
                       'module_status_registers', 'module_temperatures', 'cell_voltages']
            
            for section in sections:
                section_data = memory_map.get(section, {})
                if data_key in section_data:
                    return section_data[data_key]
            
            # 못 찾은 경우 기본값 반환
            return {'address': '-', 'unit': '', 'description': '알 수 없는 데이터'}
            
        except Exception as e:
            print(f"주소 정보 검색 오류: {e}")
            return {'address': '-', 'unit': '', 'description': '오류'}
    
    def update_real_data(self):
        """실제 장비 데이터 업데이트"""
        if not self.device_handler:
            return
        
        # 기존 데이터 클리어
        for item in self.data_tree.get_children():
            self.data_tree.delete(item)
        
        try:
            # 장비 핸들러의 상태 정보 표시
            status_info = self.device_handler.get_status()
            
            self.data_tree.insert('', tk.END, values=(
                '-', 'device_name', status_info['name'], '', '장비 이름'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'device_type', status_info['type'], '', '장비 타입'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'ip_address', status_info['ip'], '', 'IP 주소'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'port', str(status_info['port']), '', 'Modbus 포트'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'connected', '예' if status_info['connected'] else '아니오', '', '연결 상태'
            ))
            
            if status_info['last_successful_read']:
                self.data_tree.insert('', tk.END, values=(
                    '-', 'last_read', status_info['last_successful_read'], '', '마지막 읽기 시간'
                ))
            
            self.data_tree.insert('', tk.END, values=(
                '-', 'poll_interval', f"{status_info['poll_interval']}", 's', '폴링 주기'
            ))
            
        except Exception as e:
            self.data_tree.insert('', tk.END, values=(
                '-', 'error', str(e), '', '데이터 읽기 오류'
            ))
    
    def update_simulation_data(self):
        """이 메소드는 더 이상 사용하지 않습니다 - 실제 데이터만 사용"""
        pass
    
    def read_data(self):
        """데이터 읽기"""
        if self.device_handler:
            messagebox.showinfo("정보", f"{self.device_name} 데이터 읽기 요청")
        else:
            messagebox.showwarning("경고", "장비 핸들러가 없습니다")
    
    def reset_device(self):
        """장비 리셋"""
        result = messagebox.askyesno("확인", f"{self.device_name}을(를) 리셋하시겠습니까?")
        if result:
            messagebox.showinfo("정보", f"{self.device_name} 리셋 명령 전송")
    
    def write_parameter(self):
        """파라미터 쓰기"""
        address = self.write_address_entry.get()
        value = self.write_value_entry.get()
        
        if not address or not value:
            messagebox.showwarning("경고", "주소와 값을 모두 입력해주세요")
            return
        
        try:
            addr_int = int(address)
            val_int = int(value)
            
            result = messagebox.askyesno("확인", f"주소 {addr_int}에 값 {val_int}을(를) 쓰시겠습니까?")
            if result:
                messagebox.showinfo("정보", f"Write 명령 전송: 주소={addr_int}, 값={val_int}")
                
        except ValueError:
            messagebox.showerror("오류", "주소와 값은 숫자여야 합니다")


class DCDCTab(DeviceTab):
    """DCDC 탭 클래스"""
    
    def create_widgets(self):
        """DCDC 탭 위젯 생성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.parent, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 상단 정보 패널
        info_frame = ttk.LabelFrame(main_frame, text="장비 정보", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text=f"이름: {self.device_name}").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text=f"IP: {self.device_config['ip']}").grid(row=0, column=1, padx=(20, 0), sticky=tk.W)
        
        self.connection_label = ttk.Label(info_frame, text="연결 상태: 확인중", style='Status.TLabel')
        self.connection_label.grid(row=0, column=2, padx=(20, 0), sticky=tk.W)
        
        # 데이터 표시 영역
        data_frame = ttk.LabelFrame(main_frame, text="실시간 데이터", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 스크롤 가능한 데이터 트리뷰
        columns = ('address', 'parameter', 'value', 'unit', 'description')
        self.data_tree = self.create_scrollable_treeview(data_frame, columns)
        
        # 컬럼 설정
        self.data_tree.heading('address', text='주소')
        self.data_tree.heading('parameter', text='파라미터')
        self.data_tree.heading('value', text='값')
        self.data_tree.heading('unit', text='단위')
        self.data_tree.heading('description', text='설명')
        
        self.data_tree.column('address', width=80)
        self.data_tree.column('parameter', width=200)
        self.data_tree.column('value', width=150)
        self.data_tree.column('unit', width=80)
        self.data_tree.column('description', width=400)
        
        # 스크롤 가능한 제어 패널
        control_frame = self.create_scrollable_control_frame(main_frame, "DCDC 제어")
        
        # 첫 번째 행: 수동 제어 버튼들
        ttk.Button(control_frame, text="데이터 읽기", command=self.read_data).grid(row=0, column=0, padx=(0, 5), pady=5)
        ttk.Button(control_frame, text="RESET", command=self.alarm_reset).grid(row=0, column=1, padx=(5, 5), pady=5)
        
        # DCDC 전용 제어 버튼들
        ttk.Button(control_frame, text="STOP", command=self.dcdc_stop, style='Danger.TButton').grid(row=0, column=3, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="READY", command=self.dcdc_ready, style='Warning.TButton').grid(row=0, column=4, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="SOLAR", command=self.dcdc_charge, style='Success.TButton').grid(row=0, column=5, padx=(5, 5), pady=5)

        # 두 번째 행: 발전제한전력 설정
        ttk.Label(control_frame, text="발전제한전력:").grid(row=1, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.power_limit_entry = ttk.Entry(control_frame, width=10)
        self.power_limit_entry.grid(row=1, column=1, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="kW").grid(row=1, column=2, padx=(0, 10), pady=5, sticky=tk.W)
        ttk.Button(control_frame, text="전력제한 설정", command=self.set_power_limit).grid(row=1, column=3, pady=5)
        
        # 세 번째 행: Write 파라미터 입력
        ttk.Label(control_frame, text="Write 주소:").grid(row=2, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.write_address_entry = ttk.Entry(control_frame, width=10)
        self.write_address_entry.grid(row=2, column=1, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="값:").grid(row=2, column=2, padx=(5, 5), pady=5, sticky=tk.W)
        self.write_value_entry = ttk.Entry(control_frame, width=10)
        self.write_value_entry.grid(row=2, column=3, padx=(0, 10), pady=5)
        ttk.Button(control_frame, text="Write", command=self.write_parameter).grid(row=2, column=4, pady=5)
    
    
    def dcdc_stop(self):
        """DCDC 정지"""
        self.write_modbus_register(101, 85, "DCDC 정지")
    
    def alarm_reset(self):
        """알람 리셋"""
        self.write_modbus_register(100, 85, "DCDC 리셋")
    
    def dcdc_ready(self):
        """DCDC 준비"""
        self.write_modbus_register(106, 85, "DCDC 준비")
    
    def dcdc_charge(self):
        """DCDC 충전"""
        self.write_modbus_register(107, 85, "DCDC 충전")
    
    def set_power_limit(self):
        """발전제한전력 설정 (주소 2)"""
        power_str = self.power_limit_entry.get().strip()
        if not power_str:
            messagebox.showwarning("경고", "발전제한전력 값을 입력해주세요")
            return
        
        try:
            power = float(power_str)
            # DCDC 스펙에 따라 값 범위 조절 필요
            power_int = int(power * 10) # 0.1kW 단위 가정
            
            result = messagebox.askyesno("확인", f"발전제한전력을 {power}kW로 설정하시겠습니까?")
            if result:
                self.write_modbus_register(2, power_int, f"발전제한전력 설정 ({power}kW)")
                
        except ValueError as e:
            messagebox.showerror("오류", f"발전제한전력 값이 잘못되었습니다: {e}")
    
    def write_modbus_register(self, address, value, description):
        """Modbus 레지스터 쓰기 - 임시 MQTT 연결을 통한 백그라운드 서버 제어"""
        try:
            # 통합 모드에서는 임시 MQTT 연결을 통해 백그라운드 서버에 제어 명령 전송
            if self.integrated_mode and self.main_window:
                # 제어 명령 페이로드 생성
                command_data = {
                    "action": "write_register",
                    "address": address,
                    "value": value,
                    "description": description,
                    "timestamp": datetime.now().isoformat(),
                    "gui_request_id": f"{self.device_name}_{address}_{int(time.time() * 1000000)}"
                }
                
                # 임시 MQTT 연결을 통한 제어 명령 전송
                control_topic = f"pms/control/{self.device_name}/command"
                
                # 비동기 임시 MQTT 전송 실행
                def send_command():
                    import asyncio
                    try:
                        # 새 이벤트 루프에서 실행
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                            success = loop.run_until_complete(
                                self.main_window.send_mqtt_control_command_temp(control_topic, command_data)
                            )
                        else:
                            success = False
                        loop.close()
                        
                        if success:
                            messagebox.showinfo("제어 명령", f"{description} 명령을 백그라운드 서버로 전송했습니다.\n주소: {address}, 값: 0x{value:04X}")
                        else:
                            messagebox.showerror("오류", "MQTT 제어 명령 전송에 실패했습니다.")
                    except Exception as e:
                        messagebox.showerror("오류", f"제어 명령 전송 중 오류: {e}")
                
                # 별도 스레드에서 실행 (GUI 블로킹 방지)
                import threading
                thread = threading.Thread(target=send_command, daemon=True)
                thread.start()
                
            else:
                # 독립 모드에서는 직접 핸들러 접근 (기존 방식)
                if self.device_handler and hasattr(self.device_handler, 'write_register'):
                    self._execute_async_write(self.device_handler, address, value, description)
                else:
                    messagebox.showinfo("독립모드", f"{description} 명령 전송 (시뮬레이션)\n주소: {address}, 값: 0x{value:04X}")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")
    
    def _execute_async_write(self, handler, address, value, description):
        """비동기 쓰기 작업 실행"""
        try:
            # 메인 루프에서 실행되는 비동기 작업
            main_window = self.parent.master
            if hasattr(main_window, 'loop') and main_window.loop:
                # 레지스터 이름 찾기 (주소 -> 레지스터 이름 매핑)
                register_name = self._find_dcdc_register_name_by_address(address)
                if register_name:
                    # 비동기 쓰기 작업 스케줄링
                    future = asyncio.run_coroutine_threadsafe(
                        handler.write_register(register_name, value), 
                        main_window.loop
                    )
                    # 결과 확인 (타임아웃 설정)
                    result = future.result(timeout=5)
                    if result:
                        messagebox.showinfo("성공", f"{description} 명령이 성공적으로 전송되었습니다.\n주소: {address}, 값: {value}")
                    else:
                        messagebox.showerror("실패", f"{description} 명령 전송에 실패했습니다.")
                else:
                    messagebox.showerror("오류", f"주소 {address}에 해당하는 레지스터를 찾을 수 없습니다.")
            else:
                messagebox.showwarning("경고", "비동기 루프가 실행되지 않았습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"비동기 쓰기 실행 중 오류: {e}")
    
    def _find_dcdc_register_name_by_address(self, address):
        """주소로부터 DCDC 레지스터 이름 찾기"""
        try:
            # DCDC 메모리 맵에서 주소로 레지스터 이름 찾기
            memory_map = self._get_dcdc_memory_map()
            
            # 모든 섹션에서 검색
            sections = ['parameter_registers', 'metering_registers', 'control_registers']
            
            for section in sections:
                section_data = memory_map.get(section, {})
                for register_name, register_info in section_data.items():
                    if register_info.get('address') == address:
                        return register_name
            
            return None
        except Exception as e:
            print(f"DCDC 레지스터 이름 검색 오류: {e}")
            return None

    def update_data(self):
        """DCDC 데이터 업데이트"""
        # 통합 모드에서는 데이터 매니저에서 데이터 가져오기
        if hasattr(self, 'integrated_mode') and self.integrated_mode and data_manager is not None:
            device_status = data_manager.get_device_status(self.device_name)
            device_data = data_manager.get_device_data(self.device_name)
            
            # 연결 상태 업데이트
            if device_status:
                if device_status.get('connected', False):
                    last_read = device_status.get('last_successful_read')
                    if last_read:
                        self.connection_label.config(text=f"연결 상태: 연결됨 (마지막: {last_read.strftime('%H:%M:%S') if hasattr(last_read, 'strftime') else str(last_read)})", style='Connected.TLabel')
                    else:
                        self.connection_label.config(text="연결 상태: 연결됨", style='Connected.TLabel')
                else:
                    error_msg = device_status.get('last_error', '연결안됨')
                    self.connection_label.config(text=f"연결 상태: {error_msg}", style='Disconnected.TLabel')
            else:
                self.connection_label.config(text="연결 상태: 확인중", style='Status.TLabel')
            
            # 실시간 데이터 표시
            self.update_data_display(device_data)
        else:
            # 기존 로직 (독립 모드)
            if not self.device_handler:
                self.connection_label.config(text="연결 상태: 핸들러 없음", style='Disconnected.TLabel')
                return
            
            try:
                # 연결 상태 업데이트
                if self.device_handler.connected:
                    self.connection_label.config(text="연결 상태: 연결됨", style='Connected.TLabel')
                else:
                    self.connection_label.config(text="연결 상태: 연결안됨", style='Disconnected.TLabel')
                
                # 실제 데이터 읽기 시도
                self.update_real_data()
                
            except Exception as e:
                print(f"DCDC 데이터 업데이트 오류: {e}")
                self.connection_label.config(text="연결 상태: 오류", style='Disconnected.TLabel')
    
    def update_data_display(self, device_data):
        """데이터 표시 영역 업데이트"""
        # 기존 데이터 클리어
        for item in self.data_tree.get_children():
            self.data_tree.delete(item)
        
        if device_data:
            try:
                # 데이터 신선도 확인
                timestamp = device_data.get('timestamp')
                if timestamp:
                    if isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        except:
                            timestamp = datetime.now()
                    
                    age_seconds = (datetime.now() - timestamp).total_seconds()
                    if age_seconds > 300:  # 5분 초과
                        self.data_tree.insert('', tk.END, values=(
                            '-', 'status', '데이터 오래됨', '', f'{age_seconds:.0f}초 전 데이터'
                        ))
                        return
                
                # 실제 데이터 표시
                data = device_data.get('data', {})
                
                # 장비 정보 표시
                self.data_tree.insert('', tk.END, values=(
                    '-', 'device_name', data.get('device_name', 'N/A'), '', '장비 이름'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'device_type', data.get('device_type', 'N/A'), '', '장비 타입'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'ip_address', data.get('ip_address', 'N/A'), '', 'IP 주소'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'timestamp', timestamp.strftime('%H:%M:%S') if timestamp else 'N/A', '', '업데이트 시간'
                ))
                
                # DCDC 특화 센서 데이터
                sensor_data = data.get('data', {})
                if sensor_data:
                    # DCDC 메모리 맵 정보 가져오기 시도
                    memory_map = self._get_dcdc_memory_map()
                    
                    for key, value in sensor_data.items():
                        # 메모리 맵에서 주소와 단위 정보 찾기
                        addr_info = self._find_dcdc_address_info(key, memory_map)
                        address = addr_info.get('address', '-')
                        unit = addr_info.get('unit', '')
                        description = addr_info.get('description', 'DCDC 센서 데이터')
                        
                        # 16진수 주소 표시 (예: 0x0000)
                        addr_display = f"0x{address:04X}" if isinstance(address, int) else str(address)
                        
                        self.data_tree.insert('', tk.END, values=(
                            addr_display, key, str(value), unit, description
                        ))
                else:
                    self.data_tree.insert('', tk.END, values=(
                        '-', 'info', 'DCDC 데이터 로드 중', '', '잠시 기다려주세요'
                    ))
                    
            except Exception as e:
                self.data_tree.insert('', tk.END, values=(
                    '-', 'error', '데이터 파싱 오류', '', str(e)
                ))
        else:
            self.data_tree.insert('', tk.END, values=(
                '-', 'status', '데이터 없음', '', 'DCDC에서 데이터를 읽어오는 중입니다'
            ))
    
    def _get_dcdc_memory_map(self):
        """DCDC 메모리 맵 가져오기"""
        try:
            import json
            import os
            
            # DCDC 맵 파일 경로
            config_dir = os.path.join(os.path.dirname(__file__), '../../config')
            dcdc_map_path = os.path.join(config_dir, 'dcdc_map.json')
            
            if os.path.exists(dcdc_map_path):
                with open(dcdc_map_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                print(f"DCDC 맵 파일을 찾을 수 없습니다: {dcdc_map_path}")
                return {}
        except Exception as e:
            print(f"DCDC 메모리 맵 로드 오류: {e}")
            return {}
    
    def _find_dcdc_address_info(self, data_key, memory_map):
        """데이터 키에 해당하는 주소 정보 찾기"""
        try:
            # 모든 섹션에서 검색
            sections = ['parameter_registers', 'metering_registers', 'control_registers']
            
            for section in sections:
                section_data = memory_map.get(section, {})
                if data_key in section_data:
                    return section_data[data_key]
            
            # 못 찾은 경우 기본값 반환
            return {'address': '-', 'unit': '', 'description': '알 수 없는 DCDC 데이터'}
            
        except Exception as e:
            print(f"DCDC 주소 정보 검색 오류: {e}")
            return {'address': '-', 'unit': '', 'description': '오류'}
    
    def update_real_data(self):
        """실제 장비 데이터 업데이트"""
        if not self.device_handler:
            return
        
        # 기존 데이터 클리어
        for item in self.data_tree.get_children():
            self.data_tree.delete(item)
        
        try:
            # 장비 핸들러의 상태 정보 표시
            status_info = self.device_handler.get_status()
            
            self.data_tree.insert('', tk.END, values=(
                '-', 'device_name', status_info['name'], '', '장비 이름'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'device_type', status_info['type'], '', '장비 타입'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'ip_address', status_info['ip'], '', 'IP 주소'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'port', str(status_info['port']), '', 'Modbus 포트'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'connected', '예' if status_info['connected'] else '아니오', '', '연결 상태'
            ))
            
            if status_info['last_successful_read']:
                self.data_tree.insert('', tk.END, values=(
                    '-', 'last_read', status_info['last_successful_read'], '', '마지막 읽기 시간'
                ))
            
            self.data_tree.insert('', tk.END, values=(
                '-', 'poll_interval', f"{status_info['poll_interval']}", 's', '폴링 주기'
            ))
            
        except Exception as e:
            self.data_tree.insert('', tk.END, values=(
                '-', 'error', str(e), '', '데이터 읽기 오류'
            ))
    
    def read_data(self):
        """데이터 읽기"""
        messagebox.showinfo("정보", f"{self.device_name} DCDC 데이터 읽기 요청")
    
    def reset_device(self):
        """장비 리셋"""
        result = messagebox.askyesno("확인", f"{self.device_name} DCDC를 리셋하시겠습니까?")
        if result:
            messagebox.showinfo("정보", f"{self.device_name} DCDC 리셋 명령 전송")
    
    def write_parameter(self):
        """파라미터 쓰기"""
        address = self.write_address_entry.get()
        value = self.write_value_entry.get()
        
        if not address or not value:
            messagebox.showwarning("경고", "주소와 값을 모두 입력해주세요")
            return
        
        try:
            addr_int = int(address)
            val_int = int(value)
            
            result = messagebox.askyesno("확인", f"DCDC 주소 {addr_int}에 값 {val_int}을(를) 쓰시겠습니까?")
            if result:
                messagebox.showinfo("정보", f"DCDC Write 명령 전송: 주소={addr_int}, 값={val_int}")
                
        except ValueError:
            messagebox.showerror("오류", "주소와 값은 숫자여야 합니다")


class PCSTab(DeviceTab):
    """PCS 탭 클래스"""
    
    def __init__(self, parent, device_config: Dict[str, Any], handlers: List, main_window=None):
        """PCSTab 초기화"""
        super().__init__(parent, device_config, handlers, main_window)
        
        # 운전 모드 관련 변수들 초기화
        self.current_operation_mode = tk.StringVar(value="manual")
        
        # 임계값 변수들
        self.soc_high_threshold = tk.DoubleVar(value=85.0)
        self.soc_low_threshold = tk.DoubleVar(value=50.0) 
        self.soc_charge_stop_threshold = tk.DoubleVar(value=80.0)
        self.dcdc_standby_time = tk.IntVar(value=5)
        self.charging_power = tk.DoubleVar(value=30.0)
        
        # DB 설정 로더 (main_window에서 가져오기)
        self.db_config_loader = None
        if main_window and hasattr(main_window, 'db_config_loader'):
            self.db_config_loader = main_window.db_config_loader
        
        # DB 실시간 모니터링을 위한 변수들
        self.last_db_update_time = None
        self.db_monitor_active = True
        
        # 초기 설정 로드 (GUI 컴포넌트 생성 전에)
        self.load_initial_config()
        
        # DB 변경사항 모니터링 시작 (10초마다)
        if self.db_config_loader:
            self.start_db_monitoring()
        
        # GUI 컴포넌트에 DB 값 반영을 위한 플래그
        self.gui_components_created = False
    
    def create_widgets(self):
        """PCS 탭 위젯 생성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.parent, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 상단 정보 패널
        info_frame = ttk.LabelFrame(main_frame, text="장비 정보", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text=f"이름: {self.device_name}").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text=f"IP: {self.device_config['ip']}").grid(row=0, column=1, padx=(20, 0), sticky=tk.W)
        
        self.connection_label = ttk.Label(info_frame, text="연결 상태: 확인중", style='Status.TLabel')
        self.connection_label.grid(row=0, column=2, padx=(20, 0), sticky=tk.W)
        
        # 메인 컨텐츠 영역을 좌우로 분할
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True)
        
        # 좌측: 데이터 표시 및 제어 영역
        left_frame = ttk.Frame(content_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # 데이터 표시 영역
        data_frame = ttk.LabelFrame(left_frame, text="실시간 데이터", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 스크롤 가능한 데이터 트리뷰
        columns = ('address', 'parameter', 'value', 'unit', 'description')
        self.data_tree = self.create_scrollable_treeview(data_frame, columns)
        
        # 컬럼 설정
        self.data_tree.heading('address', text='주소')
        self.data_tree.heading('parameter', text='파라미터')
        self.data_tree.heading('value', text='값')
        self.data_tree.heading('unit', text='단위')
        self.data_tree.heading('description', text='설명')
        
        self.data_tree.column('address', width=80)
        self.data_tree.column('parameter', width=200)
        self.data_tree.column('value', width=150)
        self.data_tree.column('unit', width=80)
        self.data_tree.column('description', width=300)
        
        # 스크롤 가능한 제어 패널
        control_frame = self.create_scrollable_control_frame(left_frame, "PCS 제어")
        
        # 첫 번째 행: 수동 제어 버튼들
        ttk.Button(control_frame, text="데이터 읽기", command=self.read_data).grid(row=0, column=0, padx=(0, 5), pady=5)
        ttk.Button(control_frame, text="PCS 시작", command=self.pcs_start, style='Success.TButton').grid(row=0, column=1, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="PCS 정지", command=self.pcs_stop, style='Danger.TButton').grid(row=0, column=2, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="PCS 리셋", command=self.pcs_reset, style='Warning.TButton').grid(row=0, column=3, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="비상 정지", command=self.emergency_stop, style='Danger.TButton').grid(row=0, column=4, padx=(5, 5), pady=5)

        # 두 번째 행: 충전/방전 제어 및 배터리 충전 전력 설정
        ttk.Button(control_frame, text="충전 시작", command=self.pcs_charge_start, style='Success.TButton').grid(row=1, column=0, padx=(0, 5), pady=5)
        ttk.Button(control_frame, text="방전 시작", command=self.pcs_regen_start, style='Warning.TButton').grid(row=1, column=1, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="독립 운전", command=self.independent_mode, style='Success.TButton').grid(row=1, column=2, padx=(5, 5), pady=5)

        ttk.Label(control_frame, text="배터리 충전:").grid(row=1, column=3, padx=(10, 5), pady=5, sticky=tk.W)
        self.battery_charge_power_entry = ttk.Entry(control_frame, width=10)
        self.battery_charge_power_entry.grid(row=1, column=4, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="kW").grid(row=1, column=5, padx=(0, 5), pady=5, sticky=tk.W)
        
        # 세 번째 행: 배터리 충전 전력 설정 버튼 및 그리드 방전 전력 설정
        ttk.Button(control_frame, text="충전 전력 설정", command=self.set_battery_charge_power, style='Success.TButton').grid(row=2, column=0, padx=(0, 10), pady=5)

        ttk.Label(control_frame, text="그리드 방전:").grid(row=2, column=1, padx=(10, 5), pady=5, sticky=tk.W)
        self.grid_discharge_power_entry = ttk.Entry(control_frame, width=10)
        self.grid_discharge_power_entry.grid(row=2, column=2, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="kW").grid(row=2, column=3, padx=(0, 5), pady=5, sticky=tk.W)
        ttk.Button(control_frame, text="방전 전력 설정", command=self.set_grid_discharge_power, style='Warning.TButton').grid(row=2, column=4, padx=(5, 10), pady=5)
        
        # 네 번째 행: Write 파라미터 입력
        ttk.Label(control_frame, text="Write 주소:").grid(row=3, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.write_address_entry = ttk.Entry(control_frame, width=10)
        self.write_address_entry.grid(row=3, column=1, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="값:").grid(row=3, column=2, padx=(5, 5), pady=5, sticky=tk.W)
        self.write_value_entry = ttk.Entry(control_frame, width=10)
        self.write_value_entry.grid(row=3, column=3, padx=(0, 10), pady=5)
        ttk.Button(control_frame, text="Write", command=self.write_parameter).grid(row=3, column=4, pady=5)
        
        # 우측: 운전 모드 제어 패널
        right_frame = ttk.Frame(content_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        
        # 운전 모드 관련 변수들이 초기화되지 않은 경우 초기화
        if not hasattr(self, 'soc_high_threshold'):
            self.initialize_operation_variables()
        self.create_operation_control_panel(right_frame)
        
        # GUI 컴포넌트 생성 완료 플래그 설정
        self.gui_components_created = True
        
        # GUI 컴포넌트가 생성된 후 DB 값들을 다시 반영
        self.update_gui_from_db_values()
        
        # 🔧 Variable 바인딩 강화 - GUI 생성 후 Variable 값들을 다시 설정
        try:
            print("🔄 GUI 생성 완료 후 Variable 값 재설정 시작...")
            
            # DB에서 다시 로드하여 Variable에 설정 (바인딩 강화)
            if self.db_config_loader:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                config = loop.run_until_complete(self.db_config_loader.load_auto_mode_config())
                if config:
                    # Variable 값들을 다시 설정 (GUI 바인딩 강화)
                    self.soc_high_threshold.set(config.get('soc_high_threshold', 85.0))
                    self.soc_low_threshold.set(config.get('soc_low_threshold', 50.0))
                    self.soc_charge_stop_threshold.set(config.get('soc_charge_stop_threshold', 80.0))
                    self.dcdc_standby_time.set(config.get('dcdc_standby_time', 5))
                    self.charging_power.set(config.get('charging_power', 30.0))
                    
                    print("✅ GUI 생성 후 Variable 재설정 완료")
                    print(f"   📊 재설정된 값들: SOC상한={self.soc_high_threshold.get()}, SOC하한={self.soc_low_threshold.get()}, 충전정지={self.soc_charge_stop_threshold.get()}, DCDC대기={self.dcdc_standby_time.get()}, 충전전력={self.charging_power.get()}")
                    
                    # tkinter update 강제 실행으로 바인딩 적용
                    self.parent.update_idletasks()
                    print("🔄 tkinter GUI 업데이트 완료")
                    
        except Exception as e:
            print(f"❌ GUI 생성 후 Variable 재설정 중 오류: {e}")
    
    def initialize_operation_variables(self):
        """운전 모드 관련 변수들 초기화"""
        # 운전 모드 관련 변수들 초기화
        self.current_operation_mode = tk.StringVar(value="manual")
        
        # 임계값 변수들
        self.soc_high_threshold = tk.DoubleVar(value=85.0)
        self.soc_low_threshold = tk.DoubleVar(value=50.0) 
        self.soc_charge_stop_threshold = tk.DoubleVar(value=80.0)
        self.dcdc_standby_time = tk.IntVar(value=5)
        self.charging_power = tk.DoubleVar(value=30.0)
        
        # DB 설정 로더 (main_window에서 가져오기)
        self.db_config_loader = None
        if self.main_window and hasattr(self.main_window, 'db_config_loader'):
            self.db_config_loader = self.main_window.db_config_loader
        
        # 초기 설정 로드 (DB에서 운전 모드도 함께 로드됨)
        self.load_initial_config()
    
    def load_initial_config(self):
        """초기 설정 로드 (DB에서)"""
        if self.db_config_loader:
            try:
                # 비동기 함수를 동기적으로 실행
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                config = loop.run_until_complete(self.db_config_loader.load_auto_mode_config())
                if config:
                    self.soc_high_threshold.set(config.get('soc_high_threshold', 85.0))
                    self.soc_low_threshold.set(config.get('soc_low_threshold', 50.0))
                    self.soc_charge_stop_threshold.set(config.get('soc_charge_stop_threshold', 80.0))
                    self.dcdc_standby_time.set(config.get('dcdc_standby_time', 5))
                    self.charging_power.set(config.get('charging_power', 30.0))
                    
                    # 🔧 현재 운전 모드도 DB에서 로드하여 반영
                    auto_mode_enabled = config.get('auto_mode_enabled', False)
                    if auto_mode_enabled:
                        self.current_operation_mode.set("auto")
                    else:
                        self.current_operation_mode.set("manual")
                    
                    print("✅ DB에서 초기 설정 로드 완료")
                    print(f"   📊 로드된 운전 모드: {'자동' if auto_mode_enabled else '수동'}")
                else:
                    print("⚠️ DB에서 설정을 찾을 수 없음, 기본값 사용")
            except Exception as e:
                print(f"❌ DB 설정 로드 실패: {e}")
    
    def update_gui_from_db_values(self):
        """GUI 컴포넌트가 생성된 후 DB에서 불러온 값들을 GUI에 반영"""
        if not self.gui_components_created:
            return
            
        try:
            # 현재 운전 모드 라벨 업데이트 (DB에서 불러온 모드 반영)
            if hasattr(self, 'current_mode_label'):
                current_mode = self.current_operation_mode.get()
                if current_mode == "auto":
                    self.current_mode_label.config(text="자동 모드", foreground='green')
                    print("   🎛️ GUI 모드 라벨: 자동 모드로 업데이트")
                else:
                    self.current_mode_label.config(text="수동 모드", foreground='blue')
                    print("   🎛️ GUI 모드 라벨: 수동 모드로 업데이트")
            
            # 🔧 Entry 위젯에 DB 값을 직접 설정 (바인딩 문제 해결)
            try:
                # Entry 위젯이 생성되었는지 확인 후 직접 값 설정
                if hasattr(self, 'soc_high_entry') and hasattr(self, 'soc_low_entry'):
                    entry_updates = [
                        (self.soc_high_entry, self.soc_high_threshold, "SOC 상한"),
                        (self.soc_low_entry, self.soc_low_threshold, "SOC 하한"),
                        (self.soc_charge_stop_entry, self.soc_charge_stop_threshold, "SOC 충전 정지"),
                        (self.dcdc_standby_entry, self.dcdc_standby_time, "DCDC 대기시간"),
                        (self.charging_power_entry, self.charging_power, "충전 전력")
                    ]
                    
                    for entry, variable, name in entry_updates:
                        if entry and variable:
                            try:
                                # Entry 내용 클리어하고 새 값 삽입
                                entry.delete(0, tk.END)
                                entry.insert(0, str(variable.get()))
                                print(f"   📝 {name} Entry 직접 업데이트: {variable.get()}")
                            except Exception as e:
                                print(f"   ❌ {name} Entry 업데이트 오류: {e}")
                    
                    print("   🔄 모든 Entry 위젯 직접 값 설정 완료")
                else:
                    print("   ⚠️ Entry 위젯들이 아직 생성되지 않음")
                    
            except Exception as e:
                print(f"   ❌ Entry 직접 업데이트 중 오류: {e}")
            
            # 임계값들이 DB에서 불러온 값으로 설정되었는지 확인 및 로그 출력
            print(f"🔧 PCS 탭 DB → GUI 값 반영 완료:")
            print(f"   📊 SOC 상한: {self.soc_high_threshold.get()}%")
            print(f"   📊 SOC 하한: {self.soc_low_threshold.get()}%") 
            print(f"   📊 충전 정지: {self.soc_charge_stop_threshold.get()}%")
            print(f"   📊 DCDC 대기: {self.dcdc_standby_time.get()}분")
            print(f"   📊 충전 전력: {self.charging_power.get()}kW")
            print(f"   🎛️ 운전 모드: {self.current_operation_mode.get()}")
            print(f"   ✅ PCS 탭 GUI 컴포넌트 DB 값 반영 완료")
                    
        except Exception as e:
            print(f"❌ GUI DB 값 반영 중 오류: {e}")
    
    def create_operation_control_panel(self, parent):
        """운전 모드 제어 패널 생성 (PCS 탭 우측에 배치)"""
        op_frame = ttk.LabelFrame(parent, text="🎛️ 운전 모드 제어", padding="10")
        op_frame.pack(fill=tk.BOTH, expand=True)
        
        # 현재 운전 모드 표시
        mode_display_frame = ttk.Frame(op_frame)
        mode_display_frame.pack(fill=tk.X, pady=(0, 8))
        
        ttk.Label(mode_display_frame, text="현재 모드:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)
        self.current_mode_label = ttk.Label(mode_display_frame, text="수동 모드", 
                                           font=('Arial', 9, 'bold'), foreground='blue')
        self.current_mode_label.pack(anchor=tk.W, pady=(2, 0))
        
        # 운전 모드 버튼들 (세로 배치)
        mode_button_frame = ttk.Frame(op_frame)
        mode_button_frame.pack(fill=tk.X, pady=(0, 8))
        
        self.manual_mode_btn = ttk.Button(mode_button_frame, text="🔧 수동 모드", 
                                         command=self.set_manual_mode, style='ManualMode.TButton')
        self.manual_mode_btn.pack(fill=tk.X, pady=(0, 3), ipady=3)
        
        self.auto_mode_btn = ttk.Button(mode_button_frame, text="🤖 자동 모드", 
                                       command=self.set_auto_mode, style='AutoMode.TButton')
        self.auto_mode_btn.pack(fill=tk.X, ipady=3)
        
        # 구분선
        separator1 = ttk.Separator(op_frame, orient='horizontal')
        separator1.pack(fill=tk.X, pady=(8, 8))
        
        # 임계값 설정 라벨
        threshold_label = ttk.Label(op_frame, text="⚙️ 자동 운전 임계값 설정", 
                                   font=('Arial', 9, 'bold'))
        threshold_label.pack(anchor=tk.W, pady=(0, 5))
        
        # 임계값 입력 필드들을 세로로 배치 (우측 공간 활용)
        threshold_frame = ttk.Frame(op_frame)
        threshold_frame.pack(fill=tk.X, pady=(0, 8))
        
        # 각 설정을 세로로 배치하고 Entry 위젯 참조 저장
        self.soc_high_entry = self.create_threshold_input_vertical(threshold_frame, "SOC 상한 임계값:", self.soc_high_threshold, "%", 0)
        self.soc_low_entry = self.create_threshold_input_vertical(threshold_frame, "SOC 하한 임계값:", self.soc_low_threshold, "%", 1)
        self.soc_charge_stop_entry = self.create_threshold_input_vertical(threshold_frame, "SOC 충전 정지:", self.soc_charge_stop_threshold, "%", 2)
        self.dcdc_standby_entry = self.create_threshold_input_vertical(threshold_frame, "DCDC 대기시간:", self.dcdc_standby_time, "분", 3)
        self.charging_power_entry = self.create_threshold_input_vertical(threshold_frame, "충전 전력:", self.charging_power, "kW", 4)
        
        # 구분선
        separator2 = ttk.Separator(op_frame, orient='horizontal')
        separator2.pack(fill=tk.X, pady=(8, 8))
        
        # 제어 버튼들 (세로 배치)
        control_button_frame = ttk.Frame(op_frame)
        control_button_frame.pack(fill=tk.X)
        
        # DB 관련 버튼들
        save_btn = ttk.Button(control_button_frame, text="💾 저장", 
                             command=self.save_config_to_db, style='Control.TButton')
        save_btn.pack(fill=tk.X, pady=(0, 5), ipady=2)
        
        # 자동 모드 제어 버튼들
        self.auto_start_btn = ttk.Button(control_button_frame, text="🚀 자동 시작", 
                                        command=self.start_auto_mode, style='AutoMode.TButton')
        self.auto_start_btn.pack(fill=tk.X, pady=(0, 3), ipady=2)
        
        self.auto_stop_btn = ttk.Button(control_button_frame, text="🛑 자동 정지", 
                                       command=self.stop_auto_mode, style='ManualMode.TButton')
        self.auto_stop_btn.pack(fill=tk.X, ipady=2)
    
    def create_threshold_input(self, parent, label_text, variable, unit, row, col):
        """임계값 입력 필드 생성 (그리드 배치용)"""
        # 라벨
        label = ttk.Label(parent, text=label_text, width=16, anchor='w')
        label.grid(row=row, column=col, sticky="w", padx=(0, 5), pady=2)
        
        # 입력 필드
        entry = ttk.Entry(parent, textvariable=variable, width=8, justify='center')
        entry.grid(row=row, column=col+1, padx=(0, 5), pady=2)
        
        # 단위
        unit_label = ttk.Label(parent, text=unit, width=3, anchor='w')
        unit_label.grid(row=row, column=col+2, sticky="w", padx=(0, 20), pady=2)
    
    def create_threshold_input_vertical(self, parent, label_text, variable, unit, row):
        """임계값 입력 필드 생성 (세로 배치용)"""
        # 컨테이너 프레임
        container = ttk.Frame(parent)
        container.pack(fill=tk.X, pady=2)
        
        # 라벨
        label = ttk.Label(container, text=label_text, font=('Arial', 8))
        label.pack(anchor=tk.W)
        
        # 입력 필드와 단위를 가로로 배치
        input_frame = ttk.Frame(container)
        input_frame.pack(fill=tk.X, pady=(2, 0))
        
        entry = ttk.Entry(input_frame, textvariable=variable, width=10, justify='center')
        entry.pack(side=tk.LEFT, padx=(0, 5))
        
        unit_label = ttk.Label(input_frame, text=unit, font=('Arial', 8))
        unit_label.pack(side=tk.LEFT)
        
        # Entry 위젯 참조 반환
        return entry
    

    
    def save_config_to_db(self):
        """DB에 설정 저장"""
        if not self.db_config_loader:
            messagebox.showwarning("경고", "DB 연결이 설정되지 않았습니다.")
            return
        
        try:
            # 설정값 수집 (현재 운전 모드 포함)
            config_data = {
                'soc_high_threshold': self.soc_high_threshold.get(),
                'soc_low_threshold': self.soc_low_threshold.get(),
                'soc_charge_stop_threshold': self.soc_charge_stop_threshold.get(),
                'dcdc_standby_time': self.dcdc_standby_time.get(),
                'charging_power': self.charging_power.get(),
                'auto_mode_enabled': self.current_operation_mode.get() == 'auto'
            }
            
            print(f"💾 저장할 설정값:")
            print(f"   📊 SOC 상한: {config_data['soc_high_threshold']}%")
            print(f"   📊 SOC 하한: {config_data['soc_low_threshold']}%")
            print(f"   📊 충전 정지: {config_data['soc_charge_stop_threshold']}%")
            print(f"   📊 DCDC 대기: {config_data['dcdc_standby_time']}분")
            print(f"   📊 충전 전력: {config_data['charging_power']}kW")
            print(f"   🎛️ 자동 모드: {'활성화' if config_data['auto_mode_enabled'] else '비활성화'}")
            
            # 설정값 검증
            if not self.validate_config_values(config_data):
                return
            
            def save_async():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # 1단계: DB 저장 먼저 수행
                    if self.db_config_loader is not None:
                        db_success = loop.run_until_complete(self.db_config_loader.save_auto_mode_config(config_data))
                    else:
                        db_success = False
                    
                    loop.close()
                    
                    if db_success:
                        print("✅ DB 저장 성공 - MQTT 전송 시작")
                        
                        # 2단계: DB 저장 성공 후 MQTT로 임계값 설정 전송
                        def send_mqtt_after_db_save():
                            try:
                                self.send_threshold_config_mqtt(config_data)
                                # GUI 업데이트는 메인 스레드에서 실행
                                self.parent.after(0, lambda: messagebox.showinfo("성공", "설정이 DB에 저장되고 시스템에 적용되었습니다."))
                            except Exception as mqtt_e:
                                print(f"❌ MQTT 전송 중 오류: {mqtt_e}")
                                self.parent.after(0, lambda: messagebox.showwarning("부분 성공", f"DB 저장은 성공했지만 MQTT 전송 실패: {mqtt_e}"))
                        
                        # MQTT 전송을 별도 스레드에서 실행
                        import threading
                        mqtt_thread = threading.Thread(target=send_mqtt_after_db_save, daemon=True)
                        mqtt_thread.start()
                        
                    else:
                        print("❌ DB 저장 실패")
                        # GUI 업데이트는 메인 스레드에서 실행
                        self.parent.after(0, lambda: messagebox.showerror("오류", "DB 저장에 실패했습니다."))
                        
                except Exception as e:
                    print(f"❌ DB 저장 중 오류: {e}")
                    # GUI 업데이트는 메인 스레드에서 실행
                    self.parent.after(0, lambda: messagebox.showerror("오류", f"DB 저장 중 오류: {e}"))
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=save_async, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 오류: {e}")
    
    def validate_config_values(self, config_data):
        """설정값 검증"""
        try:
            # SOC 값들이 0-100 범위인지 확인
            for key in ['soc_high_threshold', 'soc_low_threshold', 'soc_charge_stop_threshold']:
                value = config_data[key]
                if not (0 <= value <= 100):
                    messagebox.showerror("입력 오류", f"{key}는 0-100 범위여야 합니다. (현재값: {value})")
                    return False
            
            # SOC 임계값 논리 확인
            if config_data['soc_low_threshold'] >= config_data['soc_high_threshold']:
                messagebox.showerror("입력 오류", "SOC 하한 임계값은 상한 임계값보다 작아야 합니다.")
                return False
            
            # DCDC 대기 시간과 충전 전력이 양수인지 확인
            if config_data['dcdc_standby_time'] <= 0:
                messagebox.showerror("입력 오류", "DCDC 대기 시간은 양수여야 합니다.")
                return False
                
            if config_data['charging_power'] <= 0:
                messagebox.showerror("입력 오류", "충전 전력은 양수여야 합니다.")
                return False
            
            return True
            
        except Exception as e:
            messagebox.showerror("검증 오류", f"설정값 검증 중 오류: {e}")
            return False
    
    def send_threshold_config_mqtt(self, config_data):
        """MQTT로 임계값 설정 전송"""
        try:
            # 사용자 요구사항에 맞는 플랫 구조 MQTT 메시지 (LOCATION 정보 포함)
            import time
            device_location = self.main_window.config.get('database', {}).get('device_location', 'Unknown') if self.main_window else 'Unknown'
            mqtt_message = {
                "soc_high_threshold": config_data.get('soc_high_threshold'),
                "soc_low_threshold": config_data.get('soc_low_threshold'), 
                "soc_charge_stop_threshold": config_data.get('soc_charge_stop_threshold'),
                "dcdc_standby_time": config_data.get('dcdc_standby_time'),
                "charging_power": config_data.get('charging_power'),
                "location": device_location,
                "timestamp": int(time.time() * 1000)  # 밀리초 타임스탬프
            }
            
            print(f"📤 MQTT 메시지 (플랫 구조):")
            print(f"   📊 soc_high_threshold: {mqtt_message['soc_high_threshold']}")
            print(f"   📊 soc_low_threshold: {mqtt_message['soc_low_threshold']}")
            print(f"   📊 soc_charge_stop_threshold: {mqtt_message['soc_charge_stop_threshold']}")
            print(f"   ⏱️ dcdc_standby_time: {mqtt_message['dcdc_standby_time']}")
            print(f"   ⚡ charging_power: {mqtt_message['charging_power']}")
            print(f"   🕐 timestamp: {mqtt_message['timestamp']}")
            
            # 임계값 설정 토픽
            threshold_topic = "pms/control/threshold_config"
            
            # 비동기 MQTT 전송
            def send_mqtt():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.main_window.send_mqtt_control_command_temp(threshold_topic, mqtt_message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        print(f"✅ 임계값 설정 MQTT 전송 완료: {threshold_topic}")
                        print(f"📝 전송된 메시지: {mqtt_message}")
                    else:
                        print(f"❌ 임계값 설정 MQTT 전송 실패")
                        
                except Exception as e:
                    print(f"❌ MQTT 전송 중 오류: {e}")
                    import traceback
                    print(f"📍 오류 상세: {traceback.format_exc()}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_mqtt, daemon=True)
            thread.start()
            
        except Exception as e:
            print(f"❌ MQTT 메시지 구성 중 오류: {e}")
            import traceback
            print(f"📍 오류 상세: {traceback.format_exc()}")
    
    def set_manual_mode(self):
        """수동 운전 모드 설정"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.main_window.config.get('database', {}).get('device_location', 'Unknown') if self.main_window else 'Unknown'
            message = {
                "mode": "basic",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_pcs_control_panel"
            }
            
            # 운전 모드 변경 토픽
            mode_topic = "pms/control/operation_mode"
            
            # 비동기 MQTT 전송
            def send_mode_change():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.main_window.send_mqtt_control_command_temp(mode_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        self.current_operation_mode.set("manual")
                        self.current_mode_label.config(text="수동 모드", foreground='blue')
                        messagebox.showinfo("모드 변경", "수동 운전 모드로 변경되었습니다.")
                    else:
                        messagebox.showerror("오류", "수동 모드 설정 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"수동 모드 설정 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_mode_change, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"수동 모드 설정 실패: {e}")
    
    def set_auto_mode(self):
        """자동 운전 모드 설정"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.main_window.config.get('database', {}).get('device_location', 'Unknown') if self.main_window else 'Unknown'
            message = {
                "mode": "auto",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_pcs_control_panel"
            }
            
            # 운전 모드 변경 토픽
            mode_topic = "pms/control/operation_mode"
            
            # 비동기 MQTT 전송
            def send_mode_change():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.main_window.send_mqtt_control_command_temp(mode_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        self.current_operation_mode.set("auto")
                        self.current_mode_label.config(text="자동 모드", foreground='green')
                        messagebox.showinfo("모드 변경", "자동 운전 모드로 변경되었습니다.")
                    else:
                        messagebox.showerror("오류", "자동 모드 설정 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"자동 모드 설정 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_mode_change, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"자동 모드 설정 실패: {e}")
    
    def start_auto_mode(self):
        """자동 모드 시작"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.main_window.config.get('database', {}).get('device_location', 'Unknown') if self.main_window else 'Unknown'
            message = {
                "command": "auto_start",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_pcs_control_panel"
            }
            
            # 자동 모드 시작 토픽
            start_topic = "pms/control/auto_mode/start"
            
            # 비동기 MQTT 전송
            def send_auto_start():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.main_window.send_mqtt_control_command_temp(start_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        messagebox.showinfo("자동 모드", "자동 운전 모드가 시작되었습니다.")
                    else:
                        messagebox.showerror("오류", "자동 모드 시작 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"자동 모드 시작 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_auto_start, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"자동 모드 시작 실패: {e}")
    
    def stop_auto_mode(self):
        """자동 모드 정지"""
        try:
            # MQTT 메시지 구성 (LOCATION 정보 포함)
            device_location = self.main_window.config.get('database', {}).get('device_location', 'Unknown') if self.main_window else 'Unknown'
            message = {
                "command": "auto_stop",
                "location": device_location,
                "timestamp": datetime.now().isoformat(),
                "source": "gui_pcs_control_panel"
            }
            
            # 자동 모드 정지 토픽
            stop_topic = "pms/control/auto_mode/stop"
            
            # 비동기 MQTT 전송
            def send_auto_stop():
                import asyncio
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                        success = loop.run_until_complete(
                            self.main_window.send_mqtt_control_command_temp(stop_topic, message)
                        )
                    else:
                        success = False
                    loop.close()
                    
                    if success:
                        messagebox.showinfo("자동 모드", "자동 운전 모드가 정지되었습니다.")
                    else:
                        messagebox.showerror("오류", "자동 모드 정지 MQTT 전송에 실패했습니다.")
                        
                except Exception as e:
                    messagebox.showerror("오류", f"자동 모드 정지 중 오류: {e}")
            
            # 별도 스레드에서 실행
            import threading
            thread = threading.Thread(target=send_auto_stop, daemon=True)
            thread.start()
            
        except Exception as e:
            messagebox.showerror("오류", f"자동 모드 정지 실패: {e}")
    
    def pcs_start(self):
        """PCS 시작 (pcs_map.json 설정 사용)"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 시작을 실행하시겠습니까?")
        if result:
            # PCS 시작 명령 (주소 21에 값 85 전송)
            self.write_modbus_register(21, 85, "PCS 시작")
    
    def pcs_stop(self):
        """PCS 정지 (pcs_map.json 설정 사용)"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 정지를 실행하시겠습니까?")
        if result:
            # PCS 정지 명령 (주소 20에 값 85 전송)
            self.write_modbus_register(20, 85, "PCS 정지")
    
    def pcs_reset(self):
        """PCS 리셋 (pcs_map.json 설정 사용)"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 리셋을 실행하시겠습니까?")
        if result:
            # PCS 리셋 명령 (주소 19에 값 85 전송)
            self.write_modbus_register(19, 85, "PCS 리셋")
    
    def pcs_charge_start(self):
        """PCS 충전 시작 (pcs_map.json 설정 사용)"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 충전을 시작하시겠습니까?")
        if result:
            # pcs_charge_start: 주소 22에 값 0x55 전송
            self.write_modbus_register(22, 0x55, "PCS 충전 시작")
    
    def pcs_regen_start(self):
        """PCS 방전 시작 (pcs_map.json 설정 사용)"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 방전을 시작하시겠습니까?")
        if result:
            # pcs_regen_start: 주소 23에 값 0x55 전송
            self.write_modbus_register(23, 0x55, "PCS 방전 시작")
    
    def alarm_reset(self):
        """PCS 리셋 (기존 호환성 유지)"""
        self.pcs_reset()
    
    def emergency_stop(self):
        """비상 정지"""
        result = messagebox.askyesno("확인", f"{self.device_name} 비상 정지를 실행하시겠습니까?")
        if result:
            # 비상 정지 명령 (주소 20에 값 85 전송)
            self.write_modbus_register(20, 85, "비상 정지")



    def set_battery_charge_power(self):
        """배터리 충전 전력 설정"""
        power_str = self.battery_charge_power_entry.get().strip()
        if not power_str:
            messagebox.showwarning("경고", "배터리 충전 전력을 입력해주세요")
            return
        
        try:
            power = float(power_str)
            if power < 0:
                messagebox.showerror("오류", "전력 값은 0 이상이어야 합니다")
                return
            
            # pcs_map.json의 scale 0.1 적용 (kW -> 0.1kW 단위)
            power_scaled = int(power * 10)
            
            result = messagebox.askyesno("확인", f"배터리 충전 전력을 {power}kW로 설정하시겠습니까?")
            if result:
                # 주소 1: battery_charge_power (pcs_map.json 참조)
                self.write_modbus_register(1, power_scaled, f"배터리 충전 전력: {power}kW")
                
        except ValueError:
            messagebox.showerror("오류", "올바른 전력 값을 입력해주세요 (숫자만)")
    
    def set_grid_discharge_power(self):
        """그리드 방전 전력 설정"""
        power_str = self.grid_discharge_power_entry.get().strip()
        if not power_str:
            messagebox.showwarning("경고", "그리드 방전 전력을 입력해주세요")
            return
        
        try:
            power = float(power_str)
            if power < 0:
                messagebox.showerror("오류", "전력 값은 0 이상이어야 합니다")
                return
            
            # pcs_map.json의 scale 0.1 적용 (kW -> 0.1kW 단위)
            power_scaled = int(power * 10)
            
            result = messagebox.askyesno("확인", f"그리드 방전 전력을 {power}kW로 설정하시겠습니까?")
            if result:
                # 주소 2: grid_discharge_power (pcs_map.json 참조)
                self.write_modbus_register(2, power_scaled, f"그리드 방전 전력: {power}kW")
                
        except ValueError:
            messagebox.showerror("오류", "올바른 전력 값을 입력해주세요 (숫자만)")

    def set_power(self):
        """출력 설정 (기존 메서드 - 호환성 유지)"""
        # 기존 코드가 있다면 배터리 충전 전력 설정으로 리디렉션
        messagebox.showinfo("안내", "배터리 충전 전력 또는 그리드 방전 전력 설정을 사용해주세요")

    
    def independent_mode(self):
        """독립 운전 모드"""
        self.write_modbus_register(24, 85, "독립 운전 모드 시작")
    
    # 핸들러 편의 함수 직접 호출 메소드들 추가
    def pcs_set_operation_mode(self, mode: str):
        """PCS 운전 모드 설정 (핸들러 직접 호출)"""
        try:
            if self.device_handler and hasattr(self.device_handler, 'set_operation_mode'):
                self._execute_async_handler_method(
                    self.device_handler.set_operation_mode, 
                    mode, 
                    f"PCS 운전 모드: {mode}"
                )
            else:
                messagebox.showwarning("경고", "PCS 핸들러의 운전 모드 설정 기능을 찾을 수 없습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"운전 모드 설정 중 오류: {e}")
    
    def pcs_reset_handler(self):
        """PCS 리셋 (핸들러 직접 호출)"""
        try:
            if self.device_handler and hasattr(self.device_handler, 'reset'):
                self._execute_async_handler_method(
                    self.device_handler.reset, 
                    None, 
                    "PCS 리셋"
                )
            else:
                messagebox.showwarning("경고", "PCS 핸들러의 리셋 기능을 찾을 수 없습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"PCS 리셋 중 오류: {e}")
    
    def pcs_bms_control(self, command: str):
        """PCS BMS 제어 (핸들러 직접 호출)"""
        try:
            handler_methods = {
                'contactor': 'bms_contactor_control',
                'reset': 'bms_reset',
                'cv_charge': 'cv_charge_start'
            }
            
            method_name = handler_methods.get(command)
            if method_name and self.device_handler and hasattr(self.device_handler, method_name):
                if command == 'contactor':
                    # 접촉기 제어는 ON/OFF 파라미터 필요
                    self._execute_async_handler_method(
                        getattr(self.device_handler, method_name), 
                        True,  # 기본값 ON
                        f"BMS 접촉기 제어"
                    )
                else:
                    self._execute_async_handler_method(
                        getattr(self.device_handler, method_name), 
                        None, 
                        f"BMS {command}"
                    )
            else:
                messagebox.showwarning("경고", f"BMS {command} 기능을 찾을 수 없습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"BMS 제어 중 오류: {e}")

    def write_modbus_register(self, address, value, description):
        """Modbus 레지스터 쓰기 - 임시 MQTT 연결을 통한 백그라운드 서버 제어"""
        try:
            # 통합 모드에서는 임시 MQTT 연결을 통해 백그라운드 서버에 제어 명령 전송
            if self.integrated_mode and self.main_window:
                # 제어 명령 페이로드 생성
                command_data = {
                    "action": "write_register",
                    "address": address,
                    "value": value,
                    "description": description,
                    "timestamp": datetime.now().isoformat(),
                    "gui_request_id": f"{self.device_name}_{address}_{int(time.time() * 1000000)}"
                }
                
                # 임시 MQTT 연결을 통한 제어 명령 전송
                control_topic = f"pms/control/{self.device_name}/command"
                
                # 비동기 임시 MQTT 전송 실행
                def send_command():
                    import asyncio
                    try:
                        # 새 이벤트 루프에서 실행
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        if self.main_window and hasattr(self.main_window, 'send_mqtt_control_command_temp'):
                            success = loop.run_until_complete(
                                self.main_window.send_mqtt_control_command_temp(control_topic, command_data)
                            )
                        else:
                            success = False
                        loop.close()
                        
                        if success:
                            messagebox.showinfo("제어 명령", f"{description} 명령을 백그라운드 서버로 전송했습니다.\n주소: {address}, 값: 0x{value:04X}")
                        else:
                            messagebox.showerror("오류", "MQTT 제어 명령 전송에 실패했습니다.")
                    except Exception as e:
                        messagebox.showerror("오류", f"제어 명령 전송 중 오류: {e}")
                
                # 별도 스레드에서 실행 (GUI 블로킹 방지)
                import threading
                thread = threading.Thread(target=send_command, daemon=True)
                thread.start()
                
            else:
                # 독립 모드에서는 시뮬레이션
                messagebox.showinfo("독립모드", f"{description} 명령 전송 (시뮬레이션)\n주소: {address}, 값: 0x{value:04X}")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")
    
    def _execute_async_write(self, handler, address, value, description):
        """비동기 쓰기 작업 실행"""
        try:
            # 메인 루프에서 실행되는 비동기 작업
            main_window = self.parent.master
            if hasattr(main_window, 'loop') and main_window.loop:
                # 레지스터 이름 찾기 (주소 -> 레지스터 이름 매핑)
                register_name = self._find_pcs_register_name_by_address(address)
                if register_name:
                    # 비동기 쓰기 작업 스케줄링
                    future = asyncio.run_coroutine_threadsafe(
                        handler.write_register(register_name, value), 
                        main_window.loop
                    )
                    # 결과 확인 (타임아웃 설정)
                    result = future.result(timeout=5)
                    if result:
                        messagebox.showinfo("성공", f"{description} 명령이 성공적으로 전송되었습니다.\n주소: {address}, 값: {value}")
                    else:
                        messagebox.showerror("실패", f"{description} 명령 전송에 실패했습니다.")
                else:
                    messagebox.showerror("오류", f"주소 {address}에 해당하는 레지스터를 찾을 수 없습니다.")
            else:
                messagebox.showwarning("경고", "비동기 루프가 실행되지 않았습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"비동기 쓰기 실행 중 오류: {e}")
    
    def _find_pcs_register_name_by_address(self, address):
        """주소로부터 PCS 레지스터 이름 찾기"""
        try:
            # PCS 메모리 맵에서 주소로 레지스터 이름 찾기
            memory_map = self._get_pcs_memory_map()
            
            # 모든 섹션에서 검색
            sections = ['parameter_registers', 'metering_registers', 'ups_registers', 'control_registers']
            
            for section in sections:
                section_data = memory_map.get(section, {})
                for register_name, register_info in section_data.items():
                    if register_info.get('address') == address:
                        return register_name
            
            return None
        except Exception as e:
            print(f"PCS 레지스터 이름 검색 오류: {e}")
            return None

    def update_data(self):
        """PCS 데이터 업데이트"""
        # 통합 모드에서는 데이터 매니저에서 데이터 가져오기
        if hasattr(self, 'integrated_mode') and self.integrated_mode and data_manager is not None:
            device_status = data_manager.get_device_status(self.device_name)
            device_data = data_manager.get_device_data(self.device_name)
            
            # 연결 상태 업데이트
            if device_status:
                if device_status.get('connected', False):
                    last_read = device_status.get('last_successful_read')
                    if last_read:
                        self.connection_label.config(text=f"연결 상태: 연결됨 (마지막: {last_read.strftime('%H:%M:%S') if hasattr(last_read, 'strftime') else str(last_read)})", style='Connected.TLabel')
                    else:
                        self.connection_label.config(text="연결 상태: 연결됨", style='Connected.TLabel')
                else:
                    error_msg = device_status.get('last_error', '연결안됨')
                    self.connection_label.config(text=f"연결 상태: {error_msg}", style='Disconnected.TLabel')
            else:
                self.connection_label.config(text="연결 상태: 확인중", style='Status.TLabel')
            
            # 실시간 데이터 표시
            self.update_data_display(device_data)
        else:
            # 기존 로직 (독립 모드)
            if not self.device_handler:
                self.connection_label.config(text="연결 상태: 핸들러 없음", style='Disconnected.TLabel')
                return
            
            try:
                # 연결 상태 업데이트
                if self.device_handler.connected:
                    self.connection_label.config(text="연결 상태: 연결됨", style='Connected.TLabel')
                else:
                    self.connection_label.config(text="연결 상태: 연결안됨", style='Disconnected.TLabel')
                
                # 실제 데이터 읽기 시도
                self.update_real_data()
                
            except Exception as e:
                print(f"PCS 데이터 업데이트 오류: {e}")
                self.connection_label.config(text="연결 상태: 오류", style='Disconnected.TLabel')
    
    def update_data_display(self, device_data):
        """데이터 표시 영역 업데이트"""
        # 기존 데이터 클리어
        for item in self.data_tree.get_children():
            self.data_tree.delete(item)
        
        if device_data:
            try:
                # 데이터 신선도 확인
                timestamp = device_data.get('timestamp')
                if timestamp:
                    if isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        except:
                            timestamp = datetime.now()
                    
                    age_seconds = (datetime.now() - timestamp).total_seconds()
                    if age_seconds > 300:  # 5분 초과
                        self.data_tree.insert('', tk.END, values=(
                            '-', 'status', '데이터 오래됨', '', f'{age_seconds:.0f}초 전 데이터'
                        ))
                        return
                
                # 실제 데이터 표시
                data = device_data.get('data', {})
                
                # 장비 정보 표시
                self.data_tree.insert('', tk.END, values=(
                    '-', 'device_name', data.get('device_name', 'N/A'), '', '장비 이름'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'device_type', data.get('device_type', 'N/A'), '', '장비 타입'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'ip_address', data.get('ip_address', 'N/A'), '', 'IP 주소'
                ))
                self.data_tree.insert('', tk.END, values=(
                    '-', 'timestamp', timestamp.strftime('%H:%M:%S') if timestamp else 'N/A', '', '업데이트 시간'
                ))
                
                # PCS 특화 센서 데이터
                sensor_data = data.get('data', {})
                if sensor_data:
                    # PCS 메모리 맵 정보 가져오기 시도
                    memory_map = self._get_pcs_memory_map()
                    
                    for key, value in sensor_data.items():
                        # 메모리 맵에서 주소와 단위 정보 찾기
                        addr_info = self._find_pcs_address_info(key, memory_map)
                        address = addr_info.get('address', '-')
                        unit = addr_info.get('unit', '')
                        description = addr_info.get('description', 'PCS 센서 데이터')
                        
                        # 16진수 주소 표시 (예: 0x0000)
                        addr_display = f"0x{address:04X}" if isinstance(address, int) else str(address)
                        
                        self.data_tree.insert('', tk.END, values=(
                            addr_display, key, str(value), unit, description
                        ))
                else:
                    self.data_tree.insert('', tk.END, values=(
                        '-', 'info', 'PCS 데이터 로드 중', '', '잠시 기다려주세요'
                    ))
                    
            except Exception as e:
                self.data_tree.insert('', tk.END, values=(
                    '-', 'error', '데이터 파싱 오류', '', str(e)
                ))
        else:
            self.data_tree.insert('', tk.END, values=(
                '-', 'status', '데이터 없음', '', 'PCS에서 데이터를 읽어오는 중입니다'
            ))
    
    def _get_pcs_memory_map(self):
        """PCS 메모리 맵 가져오기"""
        try:
            import json
            import os
            
            # PCS 맵 파일 경로
            config_dir = os.path.join(os.path.dirname(__file__), '../../config')
            pcs_map_path = os.path.join(config_dir, 'pcs_map.json')
            
            if os.path.exists(pcs_map_path):
                with open(pcs_map_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                print(f"PCS 맵 파일을 찾을 수 없습니다: {pcs_map_path}")
                return {}
        except Exception as e:
            print(f"PCS 메모리 맵 로드 오류: {e}")
            return {}
    
    def _find_pcs_address_info(self, data_key, memory_map):
        """데이터 키에 해당하는 주소 정보 찾기"""
        try:
            # 모든 섹션에서 검색
            sections = ['parameter_registers', 'metering_registers', 'ups_registers', 'control_registers']
            
            for section in sections:
                section_data = memory_map.get(section, {})
                if data_key in section_data:
                    return section_data[data_key]
            
            # 못 찾은 경우 기본값 반환
            return {'address': '-', 'unit': '', 'description': '알 수 없는 PCS 데이터'}
            
        except Exception as e:
            print(f"PCS 주소 정보 검색 오류: {e}")
            return {'address': '-', 'unit': '', 'description': '오류'}
    
    def get_unit_for_param(self, param):
        """파라미터별 단위 반환 (기존 코드와 호환성 유지)"""
        units = {
            'ac_voltage_l1': 'V',
            'ac_voltage_l2': 'V',
            'ac_voltage_l3': 'V',
            'ac_current_l1': 'A',
            'ac_current_l2': 'A',
            'ac_current_l3': 'A',
            'dc_voltage': 'V',
            'dc_current': 'A',
            'active_power': 'kW',
            'reactive_power': 'kVAR',
            'frequency': 'Hz',
            'temperature': '℃',
            'efficiency': '%'
        }
        return units.get(param, '')
    
    def update_real_data(self):
        """실제 장비 데이터 업데이트"""
        if not self.device_handler:
            return
        
        # 기존 데이터 클리어
        for item in self.data_tree.get_children():
            self.data_tree.delete(item)
        
        try:
            # 장비 핸들러의 상태 정보 표시
            status_info = self.device_handler.get_status()
            
            self.data_tree.insert('', tk.END, values=(
                '-', 'device_name', status_info['name'], '', '장비 이름'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'device_type', status_info['type'], '', '장비 타입'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'ip_address', status_info['ip'], '', 'IP 주소'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'port', str(status_info['port']), '', 'Modbus 포트'
            ))
            self.data_tree.insert('', tk.END, values=(
                '-', 'connected', '예' if status_info['connected'] else '아니오', '', '연결 상태'
            ))
            
            if status_info['last_successful_read']:
                self.data_tree.insert('', tk.END, values=(
                    '-', 'last_read', status_info['last_successful_read'], '', '마지막 읽기 시간'
                ))
            
            self.data_tree.insert('', tk.END, values=(
                '-', 'poll_interval', f"{status_info['poll_interval']}", 's', '폴링 주기'
            ))
            
        except Exception as e:
            self.data_tree.insert('', tk.END, values=(
                '-', 'error', str(e), '', '데이터 읽기 오류'
            ))
    
    def read_data(self):
        """데이터 읽기"""
        messagebox.showinfo("정보", f"{self.device_name} PCS 데이터 읽기 요청")
    
    def reset_device(self):
        """장비 리셋"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS를 리셋하시겠습니까?")
        if result:
            messagebox.showinfo("정보", f"{self.device_name} PCS 리셋 명령 전송")
    
    def run_device(self):
        """PCS 운전 시작 (기존 호환성 유지)"""
        self.pcs_start()
    
    def stop_device(self):
        """PCS 운전 정지 (기존 호환성 유지)"""
        self.pcs_stop()
    
    def write_parameter(self):
        """파라미터 쓰기"""
        address = self.write_address_entry.get()
        value = self.write_value_entry.get()
        
        if not address or not value:
            messagebox.showwarning("경고", "주소와 값을 모두 입력해주세요")
            return
        
        try:
            addr_int = int(address)
            val_int = int(value)
            
            result = messagebox.askyesno("확인", f"PCS 주소 {addr_int}에 값 {val_int}을(를) 쓰시겠습니까?")
            if result:
                messagebox.showinfo("정보", f"PCS Write 명령 전송: 주소={addr_int}, 값={val_int}")
                
        except ValueError:
            messagebox.showerror("오류", "주소와 값은 숫자여야 합니다")

    def _execute_async_handler_method(self, handler_method, param, description):
        """핸들러 메소드 비동기 실행"""
        try:
            # 메인 루프에서 실행되는 비동기 작업
            main_window = self.parent.master
            if hasattr(main_window, 'loop') and main_window.loop:
                # 파라미터 여부에 따라 다르게 호출
                if param is not None:
                    future = asyncio.run_coroutine_threadsafe(
                        handler_method(param), 
                        main_window.loop
                    )
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        handler_method(), 
                        main_window.loop
                    )
                
                # 결과 확인 (타임아웃 설정)
                result = future.result(timeout=5)
                if result:
                    messagebox.showinfo("성공", f"{description} 명령이 성공적으로 실행되었습니다.")
                else:
                    messagebox.showerror("실패", f"{description} 명령 실행에 실패했습니다.")
            else:
                messagebox.showwarning("경고", "비동기 루프가 실행되지 않았습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")
    
    def start_db_monitoring(self):
        """DB 변경사항 실시간 모니터링 시작"""
        def monitor_db_changes():
            """DB 변경사항을 주기적으로 체크하는 함수"""
            import asyncio
            import threading
            import time
            
            while self.db_monitor_active:
                try:
                    # 10초마다 DB 체크
                    time.sleep(10)
                    
                    if not self.db_monitor_active:
                        break
                    
                    # DB에서 최신 설정 가져오기
                    if not self.db_config_loader:
                        break
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    config = loop.run_until_complete(self.db_config_loader.load_auto_mode_config())
                    loop.close()
                    
                    if config:
                        # DB 업데이트 시간 체크
                        db_updated_at = config.get('db_updated_at')
                        if db_updated_at:
                            # 첫 번째 로드이거나 새로운 업데이트가 있는지 확인
                            if self.last_db_update_time is None:
                                # 첫 번째 로드 - 변경사항으로 인식하지 않음
                                print(f"ℹ️ DB 초기 설정 로드: {db_updated_at}")
                                self.last_db_update_time = db_updated_at
                            elif db_updated_at > self.last_db_update_time:
                                # 실제 변경사항 감지
                                print(f"🔔 DB 변경사항 감지! 업데이트 시간: {db_updated_at}")
                                # 메인 스레드에서 GUI 업데이트 실행
                                self.parent.after(0, lambda: self.update_gui_from_db_changes(config))
                                self.last_db_update_time = db_updated_at
                            else:
                                # 변경사항 없음 - 조용히 업데이트 시간만 갱신
                                self.last_db_update_time = db_updated_at
                        
                except Exception as e:
                    print(f"⚠️ DB 모니터링 중 오류: {e}")
                    time.sleep(5)  # 에러 시 5초 후 재시도
            
            print("🛑 DB 모니터링 종료")
        
        # DB 모니터링을 백그라운드 스레드에서 실행
        import threading
        self.db_monitor_thread = threading.Thread(target=monitor_db_changes, daemon=True)
        self.db_monitor_thread.start()
        print("🔔 DB 실시간 모니터링 시작 (10초 간격)")
    
    def update_gui_from_db_changes(self, config):
        """DB 변경사항을 GUI에 반영"""
        try:
            print("🔄 DB 변경사항을 GUI에 반영 중...")
            
            # Variable 값들 업데이트
            if config.get('soc_high_threshold') is not None:
                self.soc_high_threshold.set(config['soc_high_threshold'])
            if config.get('soc_low_threshold') is not None:
                self.soc_low_threshold.set(config['soc_low_threshold'])
            if config.get('soc_charge_stop_threshold') is not None:
                self.soc_charge_stop_threshold.set(config['soc_charge_stop_threshold'])
            if config.get('dcdc_standby_time') is not None:
                self.dcdc_standby_time.set(config['dcdc_standby_time'])
            if config.get('charging_power') is not None:
                self.charging_power.set(config['charging_power'])
            
            # 운전 모드 업데이트
            auto_mode_enabled = config.get('auto_mode_enabled', False)
            if auto_mode_enabled:
                self.current_operation_mode.set("auto")
                if hasattr(self, 'current_mode_label'):
                    self.current_mode_label.config(text="자동 모드", foreground='green')
            else:
                self.current_operation_mode.set("manual")
                if hasattr(self, 'current_mode_label'):
                    self.current_mode_label.config(text="수동 모드", foreground='blue')
            
            # Entry 위젯들 직접 업데이트 (GUI가 생성된 경우)
            if self.gui_components_created and hasattr(self, 'soc_high_entry'):
                try:
                    entry_updates = [
                        (self.soc_high_entry, self.soc_high_threshold, "SOC 상한"),
                        (self.soc_low_entry, self.soc_low_threshold, "SOC 하한"),
                        (self.soc_charge_stop_entry, self.soc_charge_stop_threshold, "SOC 충전 정지"),
                        (self.dcdc_standby_entry, self.dcdc_standby_time, "DCDC 대기시간"),
                        (self.charging_power_entry, self.charging_power, "충전 전력")
                    ]
                    
                    for entry, variable, name in entry_updates:
                        if entry and variable:
                            entry.delete(0, tk.END)
                            entry.insert(0, str(variable.get()))
                    
                    print(f"✅ DB 변경사항 GUI 반영 완료")
                    print(f"   📊 운전 모드: {'자동' if auto_mode_enabled else '수동'}")
                    print(f"   📊 SOC 상한: {config.get('soc_high_threshold')}%")
                    print(f"   📊 SOC 하한: {config.get('soc_low_threshold')}%")
                    
                except Exception as e:
                    print(f"❌ Entry 위젯 업데이트 중 오류: {e}")
            
        except Exception as e:
            print(f"❌ GUI DB 변경사항 반영 중 오류: {e}")
    
    def stop_db_monitoring(self):
        """DB 모니터링 중지"""
        self.db_monitor_active = False
        print("🛑 DB 모니터링 중지 요청")


# 테스트 실행 코드
if __name__ == "__main__":
    import sys
    import os
    
    # 패키지 경로 추가
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
    
    # 기본 설정으로 GUI 테스트
    test_config = {
        'mqtt': {
            'broker': 'localhost',
            'port': 1883,
            'client_id': 'pms_gui_test'
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
    
    print("PMS GUI 테스트 모드 시작...")
    try:
        app = PMSMainWindow(test_config)
        app.run()
    except Exception as e:
        print(f"GUI 테스트 중 오류: {e}")
        import traceback
        traceback.print_exc() 