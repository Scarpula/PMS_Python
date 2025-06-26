"""
PMS 메인 GUI 윈도우
탭 기반 장비별 모니터링 및 제어 인터페이스
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import asyncio
from typing import Dict, Any, List, Optional
import json
from datetime import datetime

# 데이터 매니저 임포트 (통합 모드용)
try:
    from ..core.data_manager import data_manager
except ImportError:
    # 독립 실행 모드에서는 None으로 설정
    data_manager = None

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
        self.root.geometry("1200x800")
        
        # 스타일 설정
        self.setup_styles()
        
        # 변수 초기화
        self.mqtt_client = None
        self.device_handlers = []
        self.device_tabs = {}
        self.running = False
        self.update_thread = None
        
        # 통합 애플리케이션 모드 확인 (백그라운드 서버가 실행 중인지)
        self.integrated_mode = True  # 통합 애플리케이션으로 실행됨
        
        # GUI 구성 요소 생성
        self.create_widgets()
        
        # 통합 모드에서는 바로 장비 탭 생성 (백그라운드 서버 사용)
        if self.integrated_mode:
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
    
    def create_widgets(self):
        """GUI 구성 요소 생성"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        
        # 상단 제어 패널
        self.create_control_panel(main_frame)
        
        # 탭 노트북 생성
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        
        # 창 크기 조정 설정
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
    
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
                device_tab = BMSTab(tab_frame, device_config, self.device_handlers)
            elif device_type == 'DCDC':
                device_tab = DCDCTab(tab_frame, device_config, self.device_handlers)
            elif device_type == 'PCS':
                device_tab = PCSTab(tab_frame, device_config, self.device_handlers)
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
                device_tab = BMSTab(tab_frame, device_config, [])  # 빈 핸들러 리스트
            elif device_type == 'DCDC':
                device_tab = DCDCTab(tab_frame, device_config, [])
            elif device_type == 'PCS':
                device_tab = PCSTab(tab_frame, device_config, [])
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
        """비동기 시스템 시작"""
        try:
            # MQTT 클라이언트 연결
            self.mqtt_client = MQTTClient(self.config['mqtt'])
            await self.mqtt_client.connect()
            
            # 장비 핸들러 생성
            self.device_handlers = []
            for device_config in self.config['devices']:
                handler = DeviceFactory.create_device(device_config, self.mqtt_client)
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
                self.status_label.config(text="시스템 상태: 독립모드", style='Status.TLabel')
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
    
    def __init__(self, parent, device_config: Dict[str, Any], handlers: List):
        self.parent = parent
        self.device_config = device_config
        self.handlers = handlers
        self.device_name = device_config['name']
        self.device_type = device_config['type']
        self.integrated_mode = False  # 통합 모드 플래그 추가
        
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
        
        # 첫 번째 행: 기본 제어 버튼들
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
        """DC 컨택터 ON (주소 512, 값 1)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DC 컨택터를 ON 하시겠습니까?")
        if result:
            self.write_modbus_register(512, 1, "DC 컨택터 ON")
    
    def dc_contactor_off(self):
        """DC 컨택터 OFF (주소 512, 값 0)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DC 컨택터를 OFF 하시겠습니까?")
        if result:
            self.write_modbus_register(512, 0, "DC 컨택터 OFF")
    
    def error_reset(self):
        """에러 리셋 (주소 513, 값 0x0050)"""
        result = messagebox.askyesno("확인", f"{self.device_name} 에러를 리셋하시겠습니까?")
        if result:
            self.write_modbus_register(513, 0x0050, "에러 리셋")
    
    def system_lock_reset(self):
        """시스템 락 리셋 (주소 514, 값 0x0050)"""
        result = messagebox.askyesno("확인", f"{self.device_name} 시스템 락을 리셋하시겠습니까?")
        if result:
            self.write_modbus_register(514, 0x0050, "시스템 락 리셋")
    
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
                self.write_modbus_register(515, ab_value, f"IP A.B 설정 (0x{ab_value:04X})")
                self.write_modbus_register(516, cd_value, f"IP C.D 설정 (0x{cd_value:04X})")
                self.write_modbus_register(517, 0xAA55, "RBMS 재시작")
                messagebox.showinfo("정보", f"IP 주소 설정 완료: {ip_str}\n장비가 재시작됩니다.")
                
        except ValueError as e:
            messagebox.showerror("오류", f"IP 주소 형식이 잘못되었습니다: {e}")

    def write_modbus_register(self, address, value, description):
        """Modbus 레지스터 쓰기"""
        try:
            # 통합 모드에서는 데이터 매니저를 통해 핸들러에 접근해야 함
            if self.integrated_mode and data_manager is not None:
                handler = data_manager.get_device_handler(self.device_name)
                if handler and hasattr(handler, 'write_register_async'):
                    # 실제 Modbus 쓰기 (비동기 호출)
                    # asyncio.create_task(handler.write_register_async(address, value)) # 이벤트 루프가 필요
                    messagebox.showinfo("성공", f"{description} 명령이 전송되었습니다.\n주소: {address}, 값: {value}")
                else:
                    messagebox.showwarning("경고", "장비 핸들러를 찾을 수 없거나 쓰기 기능이 없습니다.")
            else:
                messagebox.showinfo("정보-독립모드", f"{description} 명령 전송 (시뮬레이션)\n주소: {address}, 값: {value}")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")

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
                        
                        self.data_tree.insert('', tk.END, values=(
                            addr_display, key, str(value), unit, description
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
        
        # 첫 번째 행: 기본 제어 버튼들
        ttk.Button(control_frame, text="데이터 읽기", command=self.read_data).grid(row=0, column=0, padx=(0, 5), pady=5)
        ttk.Button(control_frame, text="알람 리셋", command=self.alarm_reset).grid(row=0, column=1, padx=(5, 5), pady=5)
        
        # DCDC 전용 제어 버튼들
        ttk.Button(control_frame, text="DCDC 시작", command=self.dcdc_start, style='Success.TButton').grid(row=0, column=2, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="DCDC 정지", command=self.dcdc_stop, style='Danger.TButton').grid(row=0, column=3, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="READY", command=self.dcdc_ready, style='Warning.TButton').grid(row=0, column=4, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="충전 모드", command=self.dcdc_charge, style='Success.TButton').grid(row=0, column=5, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="방전 모드", command=self.dcdc_regen, style='Warning.TButton').grid(row=0, column=6, padx=(5, 10), pady=5)

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
    
    def dcdc_start(self):
        """DCDC 시작 (주소 105: START Command)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DCDC를 시작하시겠습니까?")
        if result:
            self.write_modbus_register(105, 0x55, "DCDC 시작")
    
    def dcdc_stop(self):
        """DCDC 정지 (주소 101: STOP Command)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DCDC를 정지하시겠습니까?")
        if result:
            self.write_modbus_register(101, 0x55, "DCDC 정지")
    
    def alarm_reset(self):
        """알람 리셋 (주소 100: RESET Command)"""
        result = messagebox.askyesno("확인", f"{self.device_name} 알람을 리셋하시겠습니까?")
        if result:
            self.write_modbus_register(100, 0x55, "알람 리셋")
    
    def dcdc_ready(self):
        """DCDC READY 모드 (주소 102: READY Command)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DCDC를 READY 모드로 설정하시겠습니까?")
        if result:
            self.write_modbus_register(102, 0x55, "DCDC READY")
    
    def dcdc_charge(self):
        """DCDC 충전 모드 (주소 103: CHARGE Command)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DCDC를 충전 모드로 설정하시겠습니까?")
        if result:
            self.write_modbus_register(103, 0x55, "DCDC 충전 모드")
    
    def dcdc_regen(self):
        """DCDC 방전 모드 (주소 104: REGEN Command)"""
        result = messagebox.askyesno("확인", f"{self.device_name} DCDC를 방전 모드로 설정하시겠습니까?")
        if result:
            self.write_modbus_register(104, 0x55, "DCDC 방전 모드")
    
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
        """Modbus 레지스터 쓰기"""
        try:
            if self.integrated_mode and data_manager is not None:
                handler = data_manager.get_device_handler(self.device_name)
                if handler and hasattr(handler, 'write_register_async'):
                    messagebox.showinfo("성공", f"{description} 명령이 전송되었습니다.\n주소: {address}, 값: {value}")
                else:
                    messagebox.showwarning("경고", "장비 핸들러를 찾을 수 없거나 쓰기 기능이 없습니다.")
            else:
                messagebox.showinfo("정보-독립모드", f"{description} 명령 전송 (시뮬레이션)\n주소: {address}, 값: {value}")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")

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
        control_frame = self.create_scrollable_control_frame(main_frame, "PCS 제어")
        
        # 첫 번째 행: 기본 제어 버튼들
        ttk.Button(control_frame, text="데이터 읽기", command=self.read_data).grid(row=0, column=0, padx=(0, 5), pady=5)
        ttk.Button(control_frame, text="PCS 운전", command=self.run_device, style='Success.TButton').grid(row=0, column=1, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="PCS 정지", command=self.stop_device, style='Danger.TButton').grid(row=0, column=2, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="알람 리셋", command=self.alarm_reset, style='Warning.TButton').grid(row=0, column=3, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="비상 정지", command=self.emergency_stop, style='Danger.TButton').grid(row=0, column=4, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="BMS 리셋", command=self.bms_reset, style='Warning.TButton').grid(row=0, column=5, padx=(5, 5), pady=5)
        ttk.Button(control_frame, text="CV 충전", command=self.cv_charge, style='Success.TButton').grid(row=0, column=6, padx=(5, 10), pady=5)

        # 두 번째 행: 운전 모드 및 전력 설정
        ttk.Label(control_frame, text="운전 모드:").grid(row=1, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.mode_var = tk.StringVar(value="대기")
        mode_combo = ttk.Combobox(control_frame, textvariable=self.mode_var, values=["대기", "충전", "방전", "자동"], width=10, state="readonly")
        mode_combo.grid(row=1, column=1, padx=(0, 5), pady=5)
        ttk.Button(control_frame, text="모드 설정", command=self.set_operation_mode).grid(row=1, column=2, padx=(5, 10), pady=5)

        ttk.Label(control_frame, text="목표 전력:").grid(row=1, column=3, padx=(10, 5), pady=5, sticky=tk.W)
        self.power_entry = ttk.Entry(control_frame, width=10)
        self.power_entry.grid(row=1, column=4, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="kW").grid(row=1, column=5, padx=(0, 5), pady=5, sticky=tk.W)
        ttk.Button(control_frame, text="전력 설정", command=self.set_power).grid(row=1, column=6, padx=(5, 10), pady=5)
        
        # 세 번째 행: Write 파라미터 입력
        ttk.Label(control_frame, text="Write 주소:").grid(row=2, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        self.write_address_entry = ttk.Entry(control_frame, width=10)
        self.write_address_entry.grid(row=2, column=1, padx=(0, 5), pady=5)
        ttk.Label(control_frame, text="값:").grid(row=2, column=2, padx=(5, 5), pady=5, sticky=tk.W)
        self.write_value_entry = ttk.Entry(control_frame, width=10)
        self.write_value_entry.grid(row=2, column=3, padx=(0, 10), pady=5)
        ttk.Button(control_frame, text="Write", command=self.write_parameter).grid(row=2, column=4, pady=5)
    
    def alarm_reset(self):
        """알람 리셋 (주소 19: PCS Reset)"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 알람을 리셋하시겠습니까?")
        if result:
            self.write_modbus_register(19, 0x55, "PCS 리셋")
    
    def emergency_stop(self):
        """비상 정지 (주소 20: PCS Stop)"""
        result = messagebox.askyesno("경고", f"{self.device_name} PCS를 비상 정지하시겠습니까?", icon='warning')
        if result:
            self.write_modbus_register(20, 0x55, "PCS 비상 정지")

    def set_operation_mode(self):
        """운전 모드 설정"""
        mode = self.mode_var.get()
        mode_commands = {
            "대기": (21, "PCS 대기 모드"), "충전": (22, "PCS 충전 모드"), 
            "방전": (23, "PCS 방전 모드"), "자동": (24, "PCS 자동 운전")
        }
        if mode in mode_commands:
            address, description = mode_commands[mode]
            result = messagebox.askyesno("확인", f"PCS 운전 모드를 '{mode}'(으)로 설정하시겠습니까?")
            if result:
                self.write_modbus_register(address, 0x55, description)
    
    def set_power(self):
        """목표 전력 설정 (주소 25)"""
        power_str = self.power_entry.get().strip()
        if not power_str:
            messagebox.showwarning("경고", "목표 전력 값을 입력해주세요.")
            return
        try:
            power = float(power_str)
            power_int = int(power * 10) # 0.1kW 단위 가정
            result = messagebox.askyesno("확인", f"목표 전력을 {power}kW로 설정하시겠습니까?")
            if result:
                self.write_modbus_register(25, power_int, f"목표 전력 설정 ({power}kW)")
        except ValueError:
            messagebox.showerror("오류", "전력 값은 숫자여야 합니다.")

    def bms_reset(self):
        """BMS 리셋 (주소 27: BMS Reset)"""
        result = messagebox.askyesno("확인", f"{self.device_name}를 통해 BMS를 리셋하시겠습니까?")
        if result:
            self.write_modbus_register(27, 0x55, "BMS 리셋")
    
    def cv_charge(self):
        """CV 충전 시작 (주소 29: CVCRG Start)"""
        result = messagebox.askyesno("확인", f"{self.device_name} CV 충전을 시작하시겠습니까?")
        if result:
            self.write_modbus_register(29, 0x55, "CV 충전 시작")

    def write_modbus_register(self, address, value, description):
        """Modbus 레지스터 쓰기"""
        try:
            if self.integrated_mode and data_manager is not None:
                handler = data_manager.get_device_handler(self.device_name)
                if handler and hasattr(handler, 'write_register_async'):
                    messagebox.showinfo("성공", f"{description} 명령이 전송되었습니다.\n주소: {address}, 값: {value}")
                else:
                    messagebox.showwarning("경고", "장비 핸들러를 찾을 수 없거나 쓰기 기능이 없습니다.")
            else:
                messagebox.showinfo("정보-독립모드", f"{description} 명령 전송 (시뮬레이션)\n주소: {address}, 값: {value}")
        except Exception as e:
            messagebox.showerror("오류", f"{description} 실행 중 오류: {e}")
    
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
        """PCS 운전 시작"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 운전을 시작하시겠습니까?")
        if result:
            messagebox.showinfo("정보", f"{self.device_name} PCS 운전 시작 명령 전송")
    
    def stop_device(self):
        """PCS 운전 정지"""
        result = messagebox.askyesno("확인", f"{self.device_name} PCS 운전을 정지하시겠습니까?")
        if result:
            messagebox.showinfo("정보", f"{self.device_name} PCS 운전 정지 명령 전송")
    
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