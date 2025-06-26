"""
간단한 PMS GUI 테스트
실제 모든 기능을 구현하지 않고 GUI 레이아웃만 테스트
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import random


class SimplePMSGUI:
    """간단한 PMS GUI 클래스"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PMS 모니터링 및 제어 시스템 - 테스트")
        self.root.geometry("1200x800")
        
        # 스타일 설정
        self.setup_styles()
        
        # GUI 생성
        self.create_widgets()
        
        # 데이터 업데이트 타이머
        self.update_data()
    
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
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 상단 제어 패널
        self.create_control_panel(main_frame)
        
        # 탭 노트북
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        # 각 장비 탭 생성
        self.create_bms_tab()
        self.create_dcdc_tab()
        self.create_pcs_tab()
    
    def create_control_panel(self, parent):
        """상단 제어 패널 생성"""
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 시스템 상태
        ttk.Label(control_frame, text="시스템 상태:", style='Header.TLabel').pack(side=tk.LEFT, padx=(0, 10))
        self.status_label = ttk.Label(control_frame, text="실행중", style='Connected.TLabel')
        self.status_label.pack(side=tk.LEFT, padx=(0, 20))
        
        # 제어 버튼들
        ttk.Button(control_frame, text="시작", command=self.start_system, style='Control.TButton').pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(control_frame, text="정지", command=self.stop_system, style='Control.TButton').pack(side=tk.LEFT, padx=(0, 10))
        
        # MQTT 상태
        ttk.Label(control_frame, text="MQTT:", style='Header.TLabel').pack(side=tk.LEFT, padx=(20, 5))
        self.mqtt_label = ttk.Label(control_frame, text="시뮬레이션", style='Connected.TLabel')
        self.mqtt_label.pack(side=tk.LEFT)
    
    def create_bms_tab(self):
        """BMS 탭 생성"""
        bms_frame = ttk.Frame(self.notebook)
        self.notebook.add(bms_frame, text="BMS - Rack1")
        
        # 메인 컨테이너
        main_container = ttk.Frame(bms_frame, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 장비 정보
        info_frame = ttk.LabelFrame(main_container, text="BMS 장비 정보", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text="이름: Rack1_BMS").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text="IP: 192.168.1.10").grid(row=0, column=1, padx=(20, 0), sticky=tk.W)
        ttk.Label(info_frame, text="연결 상태: 시뮬레이션", style='Connected.TLabel').grid(row=0, column=2, padx=(20, 0), sticky=tk.W)
        
        # 실시간 데이터
        data_frame = ttk.LabelFrame(main_container, text="실시간 데이터", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 데이터 트리뷰
        columns = ('parameter', 'value', 'unit', 'description')
        self.bms_tree = ttk.Treeview(data_frame, columns=columns, show='headings', height=15)
        
        # 컬럼 설정
        self.bms_tree.heading('parameter', text='파라미터')
        self.bms_tree.heading('value', text='값')
        self.bms_tree.heading('unit', text='단위')
        self.bms_tree.heading('description', text='설명')
        
        self.bms_tree.column('parameter', width=150)
        self.bms_tree.column('value', width=100)
        self.bms_tree.column('unit', width=80)
        self.bms_tree.column('description', width=250)
        
        # 스크롤바
        scrollbar_bms = ttk.Scrollbar(data_frame, orient=tk.VERTICAL, command=self.bms_tree.yview)
        self.bms_tree.configure(yscrollcommand=scrollbar_bms.set)
        
        self.bms_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_bms.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 제어 패널
        control_frame = ttk.LabelFrame(main_container, text="제어", padding="10")
        control_frame.pack(fill=tk.X)
        
        # 제어 버튼들
        ttk.Button(control_frame, text="데이터 읽기", command=lambda: self.device_action("BMS", "데이터 읽기")).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(control_frame, text="리셋", command=lambda: self.device_action("BMS", "리셋")).grid(row=0, column=1, padx=(0, 10))
        ttk.Button(control_frame, text="Run", command=lambda: self.device_action("BMS", "시작")).grid(row=0, column=2, padx=(0, 10))
        ttk.Button(control_frame, text="Stop", command=lambda: self.device_action("BMS", "정지")).grid(row=0, column=3, padx=(0, 10))
        
        # Write 파라미터
        ttk.Label(control_frame, text="Write 주소:").grid(row=1, column=0, padx=(0, 5), pady=(10, 0), sticky=tk.W)
        self.bms_addr_entry = ttk.Entry(control_frame, width=10)
        self.bms_addr_entry.grid(row=1, column=1, padx=(0, 10), pady=(10, 0))
        
        ttk.Label(control_frame, text="값:").grid(row=1, column=2, padx=(0, 5), pady=(10, 0), sticky=tk.W)
        self.bms_val_entry = ttk.Entry(control_frame, width=10)
        self.bms_val_entry.grid(row=1, column=3, padx=(0, 10), pady=(10, 0))
        
        ttk.Button(control_frame, text="Write", command=self.write_bms_parameter).grid(row=1, column=4, pady=(10, 0))
    
    def create_dcdc_tab(self):
        """DCDC 탭 생성"""
        dcdc_frame = ttk.Frame(self.notebook)
        self.notebook.add(dcdc_frame, text="DCDC - Farm")
        
        # 간단한 레이아웃
        main_container = ttk.Frame(dcdc_frame, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 장비 정보
        info_frame = ttk.LabelFrame(main_container, text="DCDC 장비 정보", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text="이름: Farm_DCDC").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text="IP: 192.168.1.20").grid(row=0, column=1, padx=(20, 0), sticky=tk.W)
        ttk.Label(info_frame, text="연결 상태: 시뮬레이션", style='Connected.TLabel').grid(row=0, column=2, padx=(20, 0), sticky=tk.W)
        
        # 데이터
        data_frame = ttk.LabelFrame(main_container, text="실시간 데이터", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        columns = ('parameter', 'value', 'unit', 'description')
        self.dcdc_tree = ttk.Treeview(data_frame, columns=columns, show='headings', height=15)
        
        for col in columns:
            self.dcdc_tree.heading(col, text=col.title())
            self.dcdc_tree.column(col, width=150)
        
        self.dcdc_tree.pack(fill=tk.BOTH, expand=True)
        
        # 제어
        control_frame = ttk.LabelFrame(main_container, text="제어", padding="10")
        control_frame.pack(fill=tk.X)
        
        ttk.Button(control_frame, text="데이터 읽기", command=lambda: self.device_action("DCDC", "데이터 읽기")).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(control_frame, text="Run", command=lambda: self.device_action("DCDC", "시작")).grid(row=0, column=1, padx=(0, 10))
        ttk.Button(control_frame, text="Stop", command=lambda: self.device_action("DCDC", "정지")).grid(row=0, column=2)
    
    def create_pcs_tab(self):
        """PCS 탭 생성"""
        pcs_frame = ttk.Frame(self.notebook)
        self.notebook.add(pcs_frame, text="PCS - Unit1")
        
        # 간단한 레이아웃
        main_container = ttk.Frame(pcs_frame, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 장비 정보
        info_frame = ttk.LabelFrame(main_container, text="PCS 장비 정보", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text="이름: Unit1_PCS").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text="IP: 192.168.1.30").grid(row=0, column=1, padx=(20, 0), sticky=tk.W)
        ttk.Label(info_frame, text="연결 상태: 시뮬레이션", style='Connected.TLabel').grid(row=0, column=2, padx=(20, 0), sticky=tk.W)
        
        # 데이터
        data_frame = ttk.LabelFrame(main_container, text="실시간 데이터", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        columns = ('parameter', 'value', 'unit', 'description')
        self.pcs_tree = ttk.Treeview(data_frame, columns=columns, show='headings', height=15)
        
        for col in columns:
            self.pcs_tree.heading(col, text=col.title())
            self.pcs_tree.column(col, width=150)
        
        self.pcs_tree.pack(fill=tk.BOTH, expand=True)
        
        # 제어
        control_frame = ttk.LabelFrame(main_container, text="제어", padding="10")
        control_frame.pack(fill=tk.X)
        
        ttk.Button(control_frame, text="데이터 읽기", command=lambda: self.device_action("PCS", "데이터 읽기")).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(control_frame, text="충전 모드", command=lambda: self.device_action("PCS", "충전 모드")).grid(row=0, column=1, padx=(0, 10))
        ttk.Button(control_frame, text="방전 모드", command=lambda: self.device_action("PCS", "방전 모드")).grid(row=0, column=2, padx=(0, 10))
        ttk.Button(control_frame, text="Stop", command=lambda: self.device_action("PCS", "정지")).grid(row=0, column=3)
    
    def update_data(self):
        """시뮬레이션 데이터 업데이트"""
        # BMS 데이터 업데이트
        self.update_bms_data()
        self.update_dcdc_data()
        self.update_pcs_data()
        
        # 3초마다 업데이트
        self.root.after(3000, self.update_data)
    
    def update_bms_data(self):
        """BMS 시뮬레이션 데이터 업데이트"""
        # 기존 데이터 삭제
        for item in self.bms_tree.get_children():
            self.bms_tree.delete(item)
        
        # 새로운 시뮬레이션 데이터
        bms_data = [
            ('battery_voltage', f"{48.0 + random.uniform(-0.5, 0.5):.2f}", 'V', '배터리 전압'),
            ('current', f"{12.0 + random.uniform(-2.0, 2.0):.2f}", 'A', '전류'),
            ('soc', f"{85.0 + random.uniform(-5.0, 5.0):.1f}", '%', '충전 상태'),
            ('temperature', f"{25.0 + random.uniform(-3.0, 3.0):.1f}", '℃', '온도'),
            ('max_cell_voltage', f"{3.85 + random.uniform(-0.05, 0.05):.3f}", 'V', '최대 셀 전압'),
            ('min_cell_voltage', f"{3.82 + random.uniform(-0.05, 0.05):.3f}", 'V', '최소 셀 전압'),
            ('cell_voltage_diff', f"{0.03 + random.uniform(-0.01, 0.01):.3f}", 'V', '셀 전압 차이'),
            ('cycle_count', f"{1250 + random.randint(-10, 10)}", '', '사이클 수'),
            ('status', "정상", '', '상태'),
        ]
        
        for data in bms_data:
            self.bms_tree.insert('', tk.END, values=data)
    
    def update_dcdc_data(self):
        """DCDC 시뮬레이션 데이터 업데이트"""
        # 기존 데이터 삭제
        for item in self.dcdc_tree.get_children():
            self.dcdc_tree.delete(item)
        
        # 새로운 시뮬레이션 데이터
        dcdc_data = [
            ('input_voltage', f"{400.0 + random.uniform(-10.0, 10.0):.1f}", 'V', '입력 전압'),
            ('output_voltage', f"{48.0 + random.uniform(-1.0, 1.0):.2f}", 'V', '출력 전압'),
            ('input_current', f"{2.5 + random.uniform(-0.5, 0.5):.2f}", 'A', '입력 전류'),
            ('output_current', f"{20.0 + random.uniform(-3.0, 3.0):.2f}", 'A', '출력 전류'),
            ('efficiency', f"{95.0 + random.uniform(-1.0, 1.0):.1f}", '%', '효율'),
            ('temperature', f"{45.0 + random.uniform(-5.0, 5.0):.1f}", '℃', '온도'),
            ('input_power', f"{1000.0 + random.uniform(-100.0, 100.0):.0f}", 'W', '입력 전력'),
            ('output_power', f"{950.0 + random.uniform(-50.0, 50.0):.0f}", 'W', '출력 전력'),
        ]
        
        for data in dcdc_data:
            self.dcdc_tree.insert('', tk.END, values=data)
    
    def update_pcs_data(self):
        """PCS 시뮬레이션 데이터 업데이트"""
        # 기존 데이터 삭제
        for item in self.pcs_tree.get_children():
            self.pcs_tree.delete(item)
        
        # 새로운 시뮬레이션 데이터
        pcs_data = [
            ('ac_voltage_r', f"{220.0 + random.uniform(-5.0, 5.0):.1f}", 'V', 'AC 전압 R상'),
            ('ac_voltage_s', f"{220.0 + random.uniform(-5.0, 5.0):.1f}", 'V', 'AC 전압 S상'),
            ('ac_voltage_t', f"{220.0 + random.uniform(-5.0, 5.0):.1f}", 'V', 'AC 전압 T상'),
            ('dc_voltage', f"{800.0 + random.uniform(-20.0, 20.0):.1f}", 'V', 'DC 전압'),
            ('active_power', f"{15.0 + random.uniform(-3.0, 3.0):.2f}", 'kW', '유효 전력'),
            ('reactive_power', f"{2.0 + random.uniform(-1.0, 1.0):.2f}", 'kVAr', '무효 전력'),
            ('frequency', f"{60.0 + random.uniform(-0.1, 0.1):.2f}", 'Hz', '주파수'),
            ('operating_mode', "충전", '', '운전 모드'),
        ]
        
        for data in pcs_data:
            self.pcs_tree.insert('', tk.END, values=data)
    
    def start_system(self):
        """시스템 시작"""
        messagebox.showinfo("정보", "시스템이 시작되었습니다!")
        self.status_label.config(text="실행중", style='Connected.TLabel')
    
    def stop_system(self):
        """시스템 정지"""
        messagebox.showinfo("정보", "시스템이 정지되었습니다!")
        self.status_label.config(text="중지됨", style='Disconnected.TLabel')
    
    def device_action(self, device_type, action):
        """장비 동작"""
        messagebox.showinfo("정보", f"{device_type} - {action} 명령이 전송되었습니다!")
    
    def write_bms_parameter(self):
        """BMS 파라미터 쓰기"""
        address = self.bms_addr_entry.get()
        value = self.bms_val_entry.get()
        
        if not address or not value:
            messagebox.showwarning("경고", "주소와 값을 모두 입력해주세요")
            return
        
        try:
            addr_int = int(address)
            val_int = int(value)
            
            result = messagebox.askyesno("확인", f"BMS 주소 {addr_int}에 값 {val_int}을(를) 쓰시겠습니까?")
            if result:
                messagebox.showinfo("성공", f"BMS Write 완료: 주소={addr_int}, 값={val_int}")
                # 입력 필드 클리어
                self.bms_addr_entry.delete(0, tk.END)
                self.bms_val_entry.delete(0, tk.END)
                
        except ValueError:
            messagebox.showerror("오류", "주소와 값은 숫자여야 합니다")
    
    def run(self):
        """GUI 실행"""
        self.root.mainloop()


if __name__ == "__main__":
    print("PMS GUI 시뮬레이션 모드 시작...")
    app = SimplePMSGUI()
    app.run() 