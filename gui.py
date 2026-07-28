import sys
import os
import json
import sqlite3
import random
import string
import threading
import urllib.request
import socket
import time
import shutil
import queue
import logging
import urllib.parse
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# 알림 영역 아이콘 (System Tray) 패키지 로드 시도 및 예외 Fallback 구성
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# 전역 GUI 인스턴스 (Main.py에서 접근용)  
global_gui_instance = None

# DB 경로 및 설정
SUBSERVER_DB_PATH = "C:/SUBSERVER/subserver.db"
MAIN_SERVER_URL = "http://192.168.219.106:8000"


def ensure_activation_keys_schema():
    try:
        conn = sqlite3.connect(SUBSERVER_DB_PATH)
        c = conn.cursor()

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS activation_keys (
                phone TEXT PRIMARY KEY,
                company_name TEXT,
                company_number TEXT,
                name TEXT,
                nickname TEXT,
                email TEXT,
                division TEXT,
                department TEXT,
                position TEXT,
                key TEXT,
                created_at DATETIME,
                is_active INTEGER DEFAULT 1,
                allowed_depts TEXT
            )
            """
        )

        c.execute("PRAGMA table_info('activation_keys')")
        columns = [row[1] for row in c.fetchall()]

        if "company_number" not in columns:
            c.execute("ALTER TABLE activation_keys ADD COLUMN company_number TEXT")
        if "nickname" not in columns:
            c.execute("ALTER TABLE activation_keys ADD COLUMN nickname TEXT")
        if "allowed_depts" not in columns:
            c.execute("ALTER TABLE activation_keys ADD COLUMN allowed_depts TEXT")

        if "title" in columns and "nickname" in columns:
            c.execute("UPDATE activation_keys SET nickname = title WHERE nickname IS NULL OR nickname = ''")

        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass
    except Exception:
        pass


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 외부 타겟(연결되든 안되든 상관없음)을 지정하여 활성 인터페이스 로컬 IP를 파싱
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def register_server_to_main(biz_num: str, company_name: str, is_dynamic: int):
    local_ip = get_local_ip()
    url = f"{MAIN_SERVER_URL}/server/register"
    payload = {
        "business_number": biz_num,
        "company_name": company_name,
        "phone": get_config_value("phone"),
        "fax": get_config_value("fax"),
        "address": get_config_value("address"),
        "password": get_config_value("password"),
        "ip_address": local_ip,
        "port": 8001,
        "is_dynamic": is_dynamic,
        "motto": get_config_value("motto") or "지속성장", # 사훈(사자성어) 전송!
        "erp_base_path": get_config_value("erp_base_path") or "C:\\SUBSERVER"
    }
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    data_bytes = json.dumps(payload).encode("utf-8")
    with urllib.request.urlopen(req, data=data_bytes, timeout=5) as response:
        res_body = response.read().decode("utf-8")
        return json.loads(res_body)

def get_config_value(key: str) -> str:
    try:
        conn = sqlite3.connect(SUBSERVER_DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""

def save_config_value(key: str, value: str):
    try:
        conn = sqlite3.connect(SUBSERVER_DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"설정 {key} 저장 실패: {e}")


# 하위 호환성 유지용 래퍼 함수
def get_business_number() -> str:
    return get_config_value("business_number")

def save_business_number(business_num: str):
    save_config_value("business_number", business_num)

def mask_name(name: str) -> str:
    if not name:
        return ""
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "0"
    return name[0] + "0" * (len(name) - 2) + name[-1]

def mask_phone(phone: str) -> str:
    if not phone:
        return ""
    clean = phone.replace("-", "")
    if len(clean) >= 10:
        mid_len = len(clean) - 7
        masked_clean = clean[:3] + "*" * mid_len + clean[-4:]
        if "-" in phone:
            return f"{masked_clean[:3]}-{masked_clean[3:3+mid_len]}-{masked_clean[-4:]}"
        return masked_clean
    else:
        return phone[:3] + "***" + phone[-3:] if len(phone) > 6 else "***"

def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    parts = email.split("@")
    local = parts[0]
    domain = parts[1]
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"

def get_local_keys() -> dict:
    keys_map = {}
    try:
        conn = sqlite3.connect(SUBSERVER_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT phone, key FROM activation_keys WHERE is_active = 1")
        for row in c.fetchall():
            keys_map[row[0]] = row[1]
        conn.close()
    except Exception as e:
        print(f"로컬 키 조회 실패: {e}")
    return keys_map

def sync_employee_to_main(phone: str, action: str = "upsert"):
    """개별 직원 정보를 메인서버로 실시간 동기화합니다."""
    try:
        biz_num = get_business_number()
        password = get_config_value("password")
        if not biz_num or not password:
            print("⚠️ 사업자 번호 또는 비밀번호가 설정되지 않아 실시간 동기화를 건너뜁니다.")
            return

        name = ""
        email = ""
        division = ""
        department = ""
        position = ""
        key = ""
        is_active = 0

        if action == "upsert":
            conn = sqlite3.connect(SUBSERVER_DB_PATH)
            c = conn.cursor()
            c.execute("SELECT name, email, division, department, position, nickname, key, is_active, allowed_depts FROM activation_keys WHERE phone = ?", (phone,))
            row = c.fetchone()
            conn.close()
            if not row:
                print(f"⚠️ 동기화할 직원 정보가 존재하지 않습니다: {phone}")
                return
            name, email, division, department, position, nickname, key, is_active, allowed_depts = row
        else:
            name = "DeleteTarget"
            nickname = ""
            key = "DeleteTarget"
            is_active = 0

        url = f"{MAIN_SERVER_URL}/server/sync-employee"
        payload = {
            "business_number": biz_num,
            "company_password": password,
            "phone": phone,
            "name": name,
            "nickname": nickname or "",
            "email": email or "",
            "division": division or "",
            "department": department or "",
            "position": position or "",
            "verification_key": key,
            "is_active": int(is_active),
            "allowed_depts": allowed_depts or "",
            "action": action
        }

        req = urllib.request.Request(url, method="POST")
        req.add_header("Content-Type", "application/json")
        data_bytes = json.dumps(payload).encode("utf-8")
        with urllib.request.urlopen(req, data=data_bytes, timeout=5) as response:
            res_body = response.read().decode("utf-8")
            res_data = json.loads(res_body)
            print(f"✅ 직원 동기화 성공: {res_data}")
    except Exception as e:
        print(f"❌ 직원 동기화 중 에러 발생 (phone={phone}, action={action}): {e}")

def sync_all_employees_to_main():
    """모든 로컬 직원의 정보를 메인서버로 일괄 동기화합니다."""
    try:
        biz_num = get_business_number()
        password = get_config_value("password")
        if not biz_num or not password:
            print("⚠️ 사업자 번호 또는 비밀번호가 설정되지 않아 일괄 동기화를 건너뜁니다.")
            return

        conn = sqlite3.connect(SUBSERVER_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT phone, name, nickname, email, division, department, position, key, is_active FROM activation_keys")
        rows = c.fetchall()
        conn.close()

        print(f"🔄 총 {len(rows)}명의 직원 정보 일괄 동기화 시작...")
        for row in rows:
            phone, name, nickname, email, division, department, position, key, is_active = row
            try:
                url = f"{MAIN_SERVER_URL}/server/sync-employee"
                payload = {
                    "business_number": biz_num,
                    "company_password": password,
                    "phone": phone,
                    "name": name,
                    "nickname": nickname or "",
                    "email": email or "",
                    "division": division or "",
                    "department": department or "",
                    "position": position or "",
                    "verification_key": key,
                    "is_active": int(is_active),
                    "action": "upsert"
                }
                req = urllib.request.Request(url, method="POST")
                req.add_header("Content-Type", "application/json")
                data_bytes = json.dumps(payload).encode("utf-8")
                with urllib.request.urlopen(req, data=data_bytes, timeout=5) as response:
                    response.read()
            except Exception as e_ind:
                print(f"⚠️ 직원 {name}({phone}) 동기화 실패: {e_ind}")
        print("✅ 직원 정보 일괄 동기화 완료!")
    except Exception as e:
        print(f"❌ 직원 정보 일괄 동기화 에러: {e}")


class QueueLoggingHandler(logging.Handler):
    """파이썬 표준 로깅 이벤트를 받아 스레드 안전 대기열(Queue)로 전달하는 핸들러"""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.put(msg + "\n")
        except Exception:
            self.handleError(record)

class ThreadSafeConsole:
    """스레드 안전한 Tkinter 텍스트 위젯 로그 출력 리다이렉터"""
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.log_queue = queue.Queue()
        self._check_queue()
        self._setup_logging_handlers()

    def _setup_logging_handlers(self):
        """Uvicorn 및 Python 기본 로깅 출력을 이 큐로 연동시킴"""
        handler = QueueLoggingHandler(self.log_queue)
        # Uvicorn 포맷과 자연스럽게 어우러지도록 설정
        formatter = logging.Formatter('%(levelname)s:     %(message)s')
        handler.setFormatter(formatter)

        # 루트 로거에 핸들러 추가
        logging.getLogger().addHandler(handler)

        # uvicorn 로거들에 핸들러 추가
        for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"]:
            logger = logging.getLogger(logger_name)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

    def write(self, string):
        self.log_queue.put(string)

    def flush(self):
        pass

    def _check_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.text_widget.configure(state='normal')
                self.text_widget.insert('end', msg)
                self.text_widget.see('end')
                self.text_widget.configure(state='disabled')
        except queue.Empty:
            pass
        # 100ms마다 주기적으로 큐 감시
        self.text_widget.after(100, self._check_queue)

class SubServerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        # 모듈이 다른 엔트리포인트에서 import 되어 실행될 수 있으므로
        # 실행 시점에 반드시 로컬 DB와 테이블이 준비되어 있는지 보장합니다.
        try:
            ensure_activation_keys_schema()
        except Exception as _e:
            print(f"초기 DB 생성 오류: {_e}")
        self.title("사내 로컬 서브서버 관리 프로그램 v1.0")
        self.geometry("850x760") # 마우스 드래그 크기 조절을 반영하여 쾌적한 디폴트 세로 높이 설정
        self.configure(bg="#F4F5F7")
        self.resizable(True, True)

        # Center the window
        self.eval('tk::PlaceWindow . center')

        # 세션 변수 초기화
        self.authenticated_phones = set()
        self.fetched_users = []

        # 스타일링 정의
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # 색상 설정 및 폰트 정의
        self.style.configure(".", background="#F4F5F7", foreground="#333333", font=("맑은 고딕", 10))
        self.style.configure("TLabel", background="#F4F5F7", font=("맑은 고딕", 10))
        self.style.configure("Title.TLabel", font=("맑은 고딕", 15, "bold"), foreground="#4B0082")

        self.style.configure("TButton", font=("맑은 고딕", 10, "bold"), background="#6B3FA0", foreground="white", borderwidth=0)
        self.style.map("TButton", background=[("active", "#4B0082")])

        self.style.configure("Treeview", font=("맑은 고딕", 9), rowheight=25, fieldbackground="white")
        self.style.configure("Treeview.Heading", font=("맑은 고딕", 10, "bold"), background="#E6E8ED")

        self.create_widgets()
        self.load_initial_data()

        # stdout 및 stderr 리다이렉션 (터미널 로그 일체화)
        sys.stdout = ThreadSafeConsole(self.log_text)
        sys.stderr = ThreadSafeConsole(self.log_text)

        # 시스템 트레이 알림 영역 아이콘 바인딩
        if TRAY_AVAILABLE:
            self.protocol("WM_DELETE_WINDOW", self.hide_window)
            self.setup_system_tray()

    def setup_system_tray(self):
        """윈도우 시스템 트레이(알림 영역 아이콘) 구동"""
        try:
            # 64x64 보라색 서버 모양 아이콘 이미지 동적 빌드
            image = Image.new('RGB', (64, 64), color='#6B3FA0')
            d = ImageDraw.Draw(image)
            # 흰색 서버 모니터 형상 드로잉
            d.rectangle([16, 16, 48, 40], fill='white')
            d.rectangle([24, 44, 40, 48], fill='white')
            d.rectangle([12, 48, 52, 52], fill='white')

            # 우클릭 콘텍스트 메뉴 설정
            menu = (
                pystray.MenuItem('관리자 패널 열기', self.show_window, default=True),
                pystray.MenuItem('서버 완전 종료', self.on_exit_from_tray)
            )

            self.tray_icon = pystray.Icon("SubServer", image, "사내 로컬 서브서버", menu)

            # GUI 메인 루프를 방해하지 않도록 별도 Daemon 스레드에서 트레이 실행
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            print("🔔 [알림 영역 아이콘] 알림 영역(트레이) 아이콘 엔진 구동 성공!")
        except Exception as e:
            print(f"시스템 트레이 실행 오류: {e}")

    def show_window(self, icon=None, item=None):
        """알림 영역에서 복귀: 화면 숨김을 해제하고 화면 맨 앞으로 끌어올림"""
        self.after(0, self.deiconify)
        self.after(0, self.lift)
        self.after(0, lambda: self.state('normal'))

    def hide_window(self):
        """X 버튼 클릭 시 종료하지 않고 알림 영역(트레이)으로 최소화 숨김"""
        self.withdraw()
        # 최초 숨김 시 윈도우 OS의 작업 표시줄 우하단 트레이 알림 유도 알림창 띄우기
        messagebox.showinfo("알림 영역 백그라운드 가동",
                            "사내 로컬 서브서버가 꺼지지 않고 알림 영역(시스템 트레이)에서 실시간 백그라운드 가동 모드로 계속 실행됩니다.\n\n"
                            "패널 화면을 다시 열거나 서버를 완전히 종료하려면 작업 표시줄 우하단 트레이 아이콘을 우클릭해 주세요.")

    def on_exit_from_tray(self, icon, item):
        """트레이 '서버 완전 종료' 선택 시 완전 소멸 프로세스"""
        if messagebox.askyesno("서버 종료", "정말로 사내 로컬 서브서버를 완전히 종료하시겠습니까?\n종료 시 플러터 앱과의 모든 통신이 끊어집니다."):
            self.tray_icon.stop()
            self.after(0, self.destroy)
            # 파이썬 전체 프로세스 강제 킬 및 소멸
            os._exit(0)

    def create_widgets(self):
        # 헤더 프레임
        header_frame = tk.Frame(self, bg="#6B3FA0", height=60)
        header_frame.pack(fill="x", side="top")

        header_label = tk.Label(header_frame, text="🖥️ 사내 로컬 서브서버 및 연동 관리자 패널", font=("맑은 고딕", 14, "bold"), fg="white", bg="#6B3FA0")
        header_label.pack(pady=15, padx=20, side="left")

        # 하단 상태 표시바 (main_frame 전에 아래쪽에 먼저 배치하여 영역 확보)
        self.status_bar = tk.Label(self, text="서버 구동 준비 중...", bd=1, relief="sunken", anchor="w", bg="#E6E8ED", font=("맑은 고딕", 9), padx=10, pady=5)
        self.status_bar.pack(fill="x", side="bottom")

        # 메인 컨테이너 프레임
        main_frame = tk.Frame(self, bg="#F4F5F7", padx=20, pady=15)
        main_frame.pack(fill="both", expand=True)

        # 탭 그룹 (Notebook) 생성
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill="both", expand=True)

        # 탭 1: 사내 사업자 등록 정보 설정, 메인서버 연동회원 정보 탭
        tab_config = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_config, text=" 사내 사업자 등록 정보 설정 및 로컬 직원 등록/관리 ")

        # 탭 2: AI 검색 탭
        tab_log = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_log, text=" AI 내부 정보 검색 ")

        # 탭 3: 실시간 서버 로그 콘솔 탭
        tab_serverlog = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_serverlog, text=" 실시간 서버 로그 콘솔 ")

        # ------------------ [탭 1] 사내 사업자 등록 정보 설정 & 회원 목록 구성 ------------------

        # 1구역: 사내 사업자 등록 정보 설정 (중복 헤더 라벨을 없애기 위해 일반 ttk.Frame 사용)
        config_frame = ttk.LabelFrame(tab_config, text=" ⚙️ 사내 사업자 등록 및 서버 정보 설정 ", padding=8)
        config_frame.pack(side="top", fill="x", pady=(0, 10))

        # 그리드 열 무게 비중 설정
        config_frame.columnconfigure(1, weight=1)
        config_frame.columnconfigure(3, weight=1)

        # Row 0: 회사명 / 사훈(사자성어)
        ttk.Label(config_frame, text="회사명 :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=0, sticky="e", padx=(10, 5), pady=4)
        self.company_entry = ttk.Entry(config_frame, font=("맑은 고딕", 10))
        self.company_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=4)

        ttk.Label(config_frame, text="사훈(사자성어) :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=2, sticky="e", padx=(15, 5), pady=4)
        self.group_entry = ttk.Entry(config_frame, font=("맑은 고딕", 10))
        self.group_entry.grid(row=0, column=3, sticky="ew", padx=5, pady=4)
        self.group_entry.bind("<Button-1>", self.show_motto_menu)

        # Row 1: 사업자 등록번호 / 전화번호
        ttk.Label(config_frame, text="사업자 등록번호 :", font=("맑은 고딕", 10, "bold")).grid(row=1, column=0, sticky="e", padx=(10, 5), pady=4)
        self.biz_entry = ttk.Entry(config_frame, font=("맑은 고딕", 10))
        self.biz_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=4)

        ttk.Label(config_frame, text="전화번호 :", font=("맑은 고딕", 10, "bold")).grid(row=1, column=2, sticky="e", padx=(15, 5), pady=4)
        self.phone_entry = ttk.Entry(config_frame, font=("맑은 고딕", 10))
        self.phone_entry.grid(row=1, column=3, sticky="ew", padx=5, pady=4)

        # Row 2: 팩스번호 / 주소
        ttk.Label(config_frame, text="팩스번호 :", font=("맑은 고딕", 10, "bold")).grid(row=2, column=0, sticky="e", padx=(10, 5), pady=4)
        self.fax_entry = ttk.Entry(config_frame, font=("맑은 고딕", 10))
        self.fax_entry.grid(row=2, column=1, sticky="ew", padx=5, pady=4)

        ttk.Label(config_frame, text="주소 :", font=("맑은 고딕", 10, "bold")).grid(row=2, column=2, sticky="e", padx=(15, 5), pady=4)
        self.address_entry = ttk.Entry(config_frame, font=("맑은 고딕", 10))
        self.address_entry.grid(row=2, column=3, sticky="ew", padx=5, pady=4)

        # Row 3: 연동 비밀번호 / 유동 IP 사용 체크박스 / 저장 및 메인서버 연동
        ttk.Label(config_frame, text="연동 비밀번호 :", font=("맑은 고딕", 10, "bold")).grid(row=3, column=0, sticky="e", padx=(10, 5), pady=4)

        password_frame = ttk.Frame(config_frame)
        password_frame.grid(row=3, column=1, sticky="ew", padx=5, pady=4)

        self.password_entry = ttk.Entry(password_frame, font=("맑은 고딕", 10), show="*")
        self.password_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.unlock_btn = ttk.Button(password_frame, text="🔑 활성화", command=self.on_unlock_fields, width=10)
        self.unlock_btn.pack(side="left")

        self.dynamic_ip_var = tk.IntVar()
        self.dynamic_ip_cb = ttk.Checkbutton(config_frame, text="유동 IP 사용", variable=self.dynamic_ip_var, command=self.on_toggle_dynamic_ip)
        self.dynamic_ip_cb.grid(row=3, column=2, sticky="w", padx=15, pady=4)

        self.save_btn = ttk.Button(config_frame, text="⚙️ 저장 및 메인서버 연동", command=self.on_save_and_fetch, width=22)
        self.save_btn.grid(row=3, column=3, sticky="ew", padx=5, pady=4)

        # 1.5구역: 사내 직원 신규 등록 / 수정 폼 (직접 입력 및 보조서버 DB 저장)
        emp_register_frame = ttk.LabelFrame(tab_config, text=" ➕ 사내 직원 신규 등록 / 수정 ", padding=8)
        emp_register_frame.pack(side="top", fill="x", pady=(0, 10))

        emp_register_frame.columnconfigure(1, weight=1)
        emp_register_frame.columnconfigure(3, weight=1)
        emp_register_frame.columnconfigure(5, weight=1)

        # Row 0: 이름 / 전화번호 / 이메일
        ttk.Label(emp_register_frame, text="직원 성함 :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=0, sticky="e", padx=(10, 5), pady=4)
        self.emp_name_entry = ttk.Entry(emp_register_frame, font=("맑은 고딕", 10))
        self.emp_name_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=4)

        ttk.Label(emp_register_frame, text="호칭(필수) :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=2, sticky="e", padx=(15, 5), pady=4)
        self.emp_nickname_placeholder = "예:신부장 (대화 호칭) "
        self.emp_nickname_entry = tk.Entry(emp_register_frame, font=("맑은 고딕", 10), fg="#A9A9A9")
        self.emp_nickname_entry.grid(row=0, column=3, sticky="ew", padx=5, pady=4)
        self.emp_nickname_entry.bind("<FocusIn>", lambda e: self._clear_nickname_placeholder())
        self.emp_nickname_entry.bind("<FocusOut>", lambda e: self._apply_nickname_placeholder())
        self._apply_nickname_placeholder()

        ttk.Label(emp_register_frame, text="전화번호(필수) :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=4, sticky="e", padx=(15, 5), pady=4)
        self.emp_phone_entry = ttk.Entry(emp_register_frame, font=("맑은 고딕", 10))
        self.emp_phone_entry.grid(row=0, column=5, sticky="ew", padx=5, pady=4)

        # Row 1: 이메일 / 그룹 / 직책 / 부서 설정
        ttk.Label(emp_register_frame, text="이메일(선택) :", font=("맑은 고딕", 10, "bold")).grid(row=1, column=0, sticky="e", padx=(10, 5), pady=4)
        self.emp_email_entry = ttk.Entry(emp_register_frame, font=("맑은 고딕", 10))
        self.emp_email_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=4)

        ttk.Label(emp_register_frame, text="소속 그룹 :", font=("맑은 고딕", 10, "bold")).grid(row=1, column=2, sticky="e", padx=(15, 5), pady=4)
        self.emp_group_entry = ttk.Entry(emp_register_frame, font=("맑은 고딕", 10))
        self.emp_group_entry.grid(row=1, column=3, sticky="ew", padx=5, pady=4)
        self.emp_group_entry.insert(0, "본사")

        ttk.Label(emp_register_frame, text="직책 :", font=("맑은 고딕", 10, "bold")).grid(row=1, column=4, sticky="e", padx=(15, 5), pady=4)
        self.emp_pos_entry = ttk.Entry(emp_register_frame, font=("맑은 고딕", 10))
        self.emp_pos_entry.grid(row=1, column=5, sticky="ew", padx=5, pady=4)

        ttk.Label(emp_register_frame, text="부서 지정 :", font=("맑은 고딕", 10, "bold")).grid(row=2, column=0, sticky="e", padx=(10, 5), pady=4)

        # 부서 지정을 위한 체크박스 구성 프레임 (중복 선택 가능)
        dept_checkbox_frame = ttk.Frame(emp_register_frame)
        dept_checkbox_frame.grid(row=2, column=1, columnspan=5, sticky="ew", padx=5, pady=4)

        self.emp_dept_vars = {}
        for dname in ["영업팀", "생산팀", "구매팀", "출하팀", "관리부"]:
            self.emp_dept_vars[dname] = tk.BooleanVar(value=False)
            c = ttk.Checkbutton(dept_checkbox_frame, text=dname, variable=self.emp_dept_vars[dname])
            c.pack(side="left", padx=2)

        # Row 2: 직원 추가/수정 저장 버튼 및 폼 클리어 버튼
        btn_action_frame = ttk.Frame(emp_register_frame)
        btn_action_frame.grid(row=2, column=1, columnspan=5, sticky="e", padx=5, pady=4)

        self.emp_save_btn = ttk.Button(btn_action_frame, text="💾 직원 정보 저장 / 수정 등록", command=self.on_save_employee, width=28)
        self.emp_save_btn.pack(side="left", padx=5)

        self.emp_clear_btn = ttk.Button(btn_action_frame, text="🧹 입력란 비우기", command=self.clear_employee_inputs, width=15)
        self.emp_clear_btn.pack(side="left", padx=5)

        # 3구역: 하단 버튼 프레임 (탭 1 내부 최하단 고정 배치)
        btn_frame = tk.Frame(tab_config, bg="#F4F5F7", pady=8)
        btn_frame.pack(side="bottom", fill="x")

        self.issue_btn = ttk.Button(btn_frame, text="🔑 선택 직원 인증키 재발행", command=self.on_reissue_key, width=25)
        self.issue_btn.pack(side="left", padx=4)

        self.disable_btn = ttk.Button(btn_frame, text="🚫 선택 직원 인증 없애기", command=self.on_disable_auth, width=22)
        self.disable_btn.pack(side="left", padx=4)

        self.copy_btn = ttk.Button(btn_frame, text="📋 발급 인증키 클립보드 복사", command=self.on_copy_key, width=25)
        self.copy_btn.pack(side="left", padx=4)

        self.delete_btn = ttk.Button(btn_frame, text="❌ 선택 직원 정보 삭제", command=self.on_delete_employee, width=22)
        self.delete_btn.pack(side="left", padx=4)

        self.refresh_btn = ttk.Button(btn_frame, text="🔄 직원 목록 새로고침", command=self.fetch_and_render_members, width=20)
        self.refresh_btn.pack(side="right", padx=4)

        # 2구역: 메인 회원 목록 조회 및 인증키 관리 프레임
        members_frame = ttk.LabelFrame(tab_config, text=" 👥 사내 직원 정보 및 인증키 관리 (로컬 PC 저장) ", padding=12)
        members_frame.pack(side="top", fill="both", expand=True)

        # 트리뷰 테이블 설정
        columns = ("name", "nickname", "phone", "email", "division", "dept", "position", "key")
        self.tree = ttk.Treeview(members_frame, columns=columns, show="headings", selectmode="browse", height=10)

        self.tree.heading("name", text="성함")
        self.tree.heading("nickname", text="호칭")
        self.tree.heading("phone", text="전화번호")
        self.tree.heading("email", text="이메일")
        self.tree.heading("division", text="그룹")
        self.tree.heading("dept", text="부서")
        self.tree.heading("position", text="직책")
        self.tree.heading("key", text="발급된 인증키")

        self.tree.column("name", width=75, anchor="center")
        self.tree.column("nickname", width=70, anchor="center")
        self.tree.column("phone", width=105, anchor="center")
        self.tree.column("email", width=150, anchor="w")
        self.tree.column("division", width=80, anchor="center")
        self.tree.column("dept", width=80, anchor="center")
        self.tree.column("position", width=75, anchor="center")
        self.tree.column("key", width=120, anchor="center")

        # 스크롤바 추가
        scrollbar = ttk.Scrollbar(members_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 트리뷰 선택 시 폼 로드 바인딩
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # ------------------ [탭 2] AI 내부 정보 검색 (질문/응답 + 세부 관리 하위 탭) ------------------
        ai_frame = ttk.LabelFrame(tab_log, text=" 🤖 AI 내부 정보 검색 ", padding=10)
        ai_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(ai_frame, text="질문 :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=0, sticky="e", padx=(0, 5), pady=4)
        self.ai_query_entry = ttk.Entry(ai_frame, font=("맑은 고딕", 10))
        self.ai_query_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=4)
        self.ai_query_entry.bind("<Return>", lambda e: self.on_ai_search())

        ttk.Label(ai_frame, text="부서 :", font=("맑은 고딕", 10, "bold")).grid(row=0, column=2, sticky="e", padx=(10, 5), pady=4)
        self.ai_department_var = tk.StringVar(value="SALES")
        self.ai_department_combo = ttk.Combobox(ai_frame, textvariable=self.ai_department_var, values=["SALES", "PRODUCTION", "PROCUREMENT", "SHIPPING", "MANAGEMENT"], width=16, state="readonly")
        self.ai_department_combo.grid(row=0, column=3, sticky="ew", padx=5, pady=4)

        self.ai_search_btn = ttk.Button(ai_frame, text="🔎 검색", command=self.on_ai_search, width=12)
        self.ai_search_btn.grid(row=0, column=4, padx=5, pady=4)

        # AI 모델 선택 행
        ttk.Label(ai_frame, text="AI 모델 :", font=("맑은 고딕", 10, "bold")).grid(row=1, column=0, sticky="e", padx=(0, 5), pady=4)
        self.ai_model_var = tk.StringVar(value=get_config_value("local_ai_model") or "qwen2.5:7b")
        self.ai_model_combo = ttk.Combobox(ai_frame, textvariable=self.ai_model_var, width=28, state="readonly")
        self.ai_model_combo.grid(row=1, column=1, sticky="w", padx=5, pady=4)
        self.ai_model_combo.bind("<<ComboboxSelected>>", self.on_select_ai_model)

        ttk.Button(ai_frame, text="🔄 모델목록", command=self.refresh_ai_models, width=12).grid(row=1, column=2, padx=5, pady=4)
        ai_frame.columnconfigure(1, weight=1)

        # ------------------ AI 검색 세부 관리 하위 탭 ------------------
        self.ai_notebook = ttk.Notebook(tab_log)
        self.ai_notebook.pack(fill="both", expand=True, pady=(0, 8))

        # [하위탭 1] 질문/응답
        qa_tab = ttk.Frame(self.ai_notebook, padding=8)
        self.ai_notebook.add(qa_tab, text=" 💬 질문/응답 ")
        qa_wrap = ttk.Frame(qa_tab)
        qa_wrap.pack(fill="both", expand=True)
        qa_scroll = ttk.Scrollbar(qa_wrap, orient="vertical")
        self.ai_result_text = tk.Text(qa_wrap, wrap="word", bg="#F9FAFC", fg="#222222", font=("맑은 고딕", 10), yscrollcommand=qa_scroll.set)
        qa_scroll.config(command=self.ai_result_text.yview)
        qa_scroll.pack(side="right", fill="y")
        self.ai_result_text.pack(side="left", fill="both", expand=True)

        # [하위탭 2] 검색 결과 테이블 (동적 컬럼)
        table_tab = ttk.Frame(self.ai_notebook, padding=8)
        self.ai_notebook.add(table_tab, text=" 📊 검색 결과 테이블 ")
        tree_wrap = ttk.Frame(table_tab)
        tree_wrap.pack(fill="both", expand=True)
        self.ai_table_tree = ttk.Treeview(tree_wrap, show="headings", height=10)
        ai_tree_scroll_y = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.ai_table_tree.yview)
        ai_tree_scroll_x = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.ai_table_tree.xview)
        self.ai_table_tree.configure(yscrollcommand=ai_tree_scroll_y.set, xscrollcommand=ai_tree_scroll_x.set)
        ai_tree_scroll_y.pack(side="right", fill="y")
        ai_tree_scroll_x.pack(side="bottom", fill="x")
        self.ai_table_tree.pack(side="left", fill="both", expand=True)

        # 마지막 검색 응답 보관 (피드백/검토용)
        self._last_ai_response = None

        # 시작 시 설치된 모델 목록 로드 (백그라운드)
        threading.Thread(target=self.refresh_ai_models, daemon=True).start()

        # 서버 로그 콘솔 (단독 탭 분리)
        log_frame = ttk.Frame(tab_serverlog, padding=5)
        log_frame.pack(fill="both", expand=True)

        log_scroll = ttk.Scrollbar(log_frame, orient="vertical")
        self.log_text = tk.Text(log_frame, wrap="word", bg="#1E1E1E", fg="#F1F1F1", font=("Consolas", 10), yscrollcommand=log_scroll.set)
        log_scroll.config(command=self.log_text.yview)

        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def on_ai_search(self):
        """로컬 FastAPI 서버에 AI 검색 요청을 백그라운드로 보내 결과를 표시합니다."""
        query = self.ai_query_entry.get().strip()
        if not query:
            messagebox.showwarning("입력 필요", "검색할 질문을 입력해주세요.")
            return

        dept = self.ai_department_var.get().strip() or "SALES"
        self.ai_search_btn.config(state="disabled", text="검색 중…")
        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_text.insert(tk.END, f"'{query}' 검색 중… (모델: {self.ai_model_var.get()})")

        threading.Thread(target=self._run_ai_search, args=(query, dept), daemon=True).start()

    def _run_ai_search(self, query: str, dept: str):
        """(백그라운드) 검색 요청 수행 후 메인 스레드로 결과를 전달합니다."""
        payload = json.dumps({"query": query, "department": dept}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:8001/ai/search", data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-API-Key", "MyCompanySecretKey1234!")
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.after(0, self._render_ai_result, data)
        except Exception as e:
            self.after(0, self._render_ai_error, str(e))

    def _render_ai_error(self, msg: str):
        self.ai_search_btn.config(state="normal", text="🔎 검색")
        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_text.insert(tk.END, f"검색 요청 실패: {msg}\n서버 실행 상태와 DB/엑셀 파일 위치를 확인해주세요.")

    def _render_ai_result(self, data: dict):
        """검색 응답(JSON)을 요약 텍스트 + 결과 테이블로 렌더링합니다."""
        self.ai_search_btn.config(state="normal", text="🔎 검색")
        self._last_ai_response = data
        answer_json = data.get("answer_json") or {}
        engine = data.get("engine", "?")

        # 1) 요약 텍스트 (원본 JSON 덤프 대신 선택된 경로 + 요약만 표시)
        summary = answer_json.get("summary", "")
        table = answer_json.get("table", {}) or {}
        src_file = table.get("source_file", "")
        src_table = table.get("source_table", "")
        headers = table.get("headers", []) or []
        rows = table.get("rows", []) or []
        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_text.insert(tk.END, f"[엔진: {engine}]\n")
        if src_file or src_table:
            self.ai_result_text.insert(tk.END, f"선택 경로: {src_file}\\{src_table}\n")
        self.ai_result_text.insert(tk.END, f"표시 행 수: {len(rows)}건\n")
        if summary:
            self.ai_result_text.insert(tk.END, f"{summary}\n")
        self.ai_result_text.insert(tk.END, "\n※ 전체 데이터는 [검색 결과 테이블] 탭에서 확인하세요.")

        # 2) 결과 테이블 렌더링
        self._render_ai_table(headers, rows)

    def _render_ai_table(self, headers, rows):
        """Treeview를 동적 컬럼으로 재구성하여 결과 행을 채웁니다."""
        tree = self.ai_table_tree
        tree.delete(*tree.get_children())
        if not headers:
            tree["columns"] = ()
            return
        tree["columns"] = headers
        for h in headers:
            tree.heading(h, text=str(h))
            tree.column(h, width=max(80, min(220, len(str(h)) * 14)), anchor="w")
        for r in rows:
            values = list(r) + [""] * (len(headers) - len(r))
            tree.insert("", "end", values=values[:len(headers)])

    def display_external_ai_result(self, query: str, data: dict):
        """앱으로부터 요청받은 AI 검색 결과를 GUI 탭에도 실시간 표시합니다."""
        answer_json = data.get("answer_json") or {}
        engine = data.get("engine", "Unknown")
        dept = data.get("department", "Unknown")

        summary = answer_json.get("summary", "")
        table = answer_json.get("table", {}) or {}
        src_file = table.get("source_file", "")
        src_table = table.get("source_table", "")
        headers = table.get("headers", []) or []
        rows = table.get("rows", []) or []

        # 1) 질문/응답 탭 업데이트
        self.ai_result_text.config(state="normal")
        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_text.insert(tk.END, f"📢 [외부 앱 요청 감지]\n")
        self.ai_result_text.insert(tk.END, f"질문: {query}\n")
        self.ai_result_text.insert(tk.END, f"부서: {dept} | 엔진: {engine}\n")
        self.ai_result_text.insert(tk.END, "-" * 40 + "\n")
        
        if src_file or src_table:
            self.ai_result_text.insert(tk.END, f"📍 로컬 AI 탐색 결과: {src_file}\\{src_table}\n")
        
        self.ai_result_text.insert(tk.END, f"📊 추출된 데이터: {len(rows)}건\n")
        if summary:
            self.ai_result_text.insert(tk.END, f"📝 요약: {summary}\n")
            
        self.ai_result_text.insert(tk.END, "\n* 이 정보는 클라우드 AI로 전달되어 최종 가공됩니다.")
        self.ai_result_text.config(state="disabled")

        # 2) 결과 테이블 탭 업데이트
        self._render_ai_table(headers, rows)

    # ------------------ AI 모델 / 프롬프트 / 튜닝 설정 ------------------

    def _get_ollama_models(self) -> list:
        """Ollama에 설치된 모델 목록을 조회합니다. 실패 시 빈 리스트."""
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in body.get("models", []) if m.get("name")]
        except Exception:
            return []

    def refresh_ai_models(self):
        """설치된 모델 목록을 콤보박스에 반영합니다 (백그라운드에서 호출 가능)."""
        models = self._get_ollama_models()

        def _apply():
            saved = get_config_value("local_ai_model")
            values = models or ([saved] if saved else ["qwen2.5:7b"])
            self.ai_model_combo["values"] = values
            if saved and saved in values:
                self.ai_model_var.set(saved)
            elif values:
                self.ai_model_var.set(values[0])

        self.after(0, _apply)

    def on_select_ai_model(self, event=None):
        """콤보박스에서 모델 선택 시 즉시 config에 저장합니다."""
        model = self.ai_model_var.get().strip()
        if model:
            save_config_value("local_ai_model", model)
            messagebox.showinfo("AI 모델 변경", f"로컬 AI 모델이 '{model}' 로 저장되었습니다.\n(서버가 다음 검색부터 적용)")

    def _update_emp_group_entry(self, group_val):
        """사내 직원 신규 등록 폼의 소속 그룹 필드를 자동 입력합니다 (비활성화 연동 풀기)."""
        self.emp_group_entry.config(state="normal")
        self.emp_group_entry.delete(0, tk.END)
        self.emp_group_entry.insert(0, group_val)

    def _apply_nickname_placeholder(self):
        if not self.emp_nickname_entry.get().strip():
            self.emp_nickname_entry.config(fg="#A9A9A9")
            self.emp_nickname_entry.delete(0, tk.END)
            self.emp_nickname_entry.insert(0, self.emp_nickname_placeholder)

    def _clear_nickname_placeholder(self):
        if self.emp_nickname_entry.get().strip() == self.emp_nickname_placeholder:
            self.emp_nickname_entry.delete(0, tk.END)
            self.emp_nickname_entry.config(fg="#000000")

    def show_motto_menu(self, event):
        if self.group_entry.cget("state") == "disabled":
            return

        motto_menu = tk.Menu(self, tearoff=0, font=("맑은 고딕", 10))
        mottos = [
            ("성실정진(誠實精進) – 성실히 노력하며 나아간다", "성실정진"),
            ("도전무한(挑戰無限) – 도전에는 한계가 없다", "도전무한"),
            ("신뢰우선(信賴優先) – 신뢰를 최우선 가치로", "신뢰우선"),
            ("창의혁신(創意革新) – 창의로 혁신을 이끈다", "창의혁신"),
            ("일심동행(一心同行) – 한마음으로 함께 나아간다", "일심동행"),
            ("정도경영(正道經營) – 바른 길로 경영한다", "정도경영"),
            ("열정성취(熱情成就) – 열정으로 성취를 이룬다", "열정성취"),
            ("지속성장(持續成長) – 꾸준히 성장하는 기업", "지속성장"),
            ("고객제일(顧客第一) – 고객을 최우선으로", "고객제일")
        ]
        for label, val in mottos:
            motto_menu.add_command(label=label, command=lambda v=val: self.select_motto(v))
        motto_menu.post(event.x_root, event.y_root)

    def select_motto(self, val):
        self.group_entry.delete(0, tk.END)
        self.group_entry.insert(0, val)

    def set_config_fields_state(self, state):
        """사내 사업자 등록 정보 입력창들의 활성화/비활성화 상태를 일괄 제어합니다."""
        self.company_entry.config(state=state)
        self.group_entry.config(state=state)
        self.biz_entry.config(state=state)
        self.phone_entry.config(state=state)
        self.fax_entry.config(state=state)
        self.address_entry.config(state=state)
        self.dynamic_ip_cb.config(state=state)
        self.save_btn.config(state=state)

    def on_unlock_fields(self):
        """연동 비밀번호를 대조 검증하여 설정 입력창을 편집 가능하게 활성화시킵니다."""
        entered_pw = self.password_entry.get().strip()
        saved_pw = get_config_value("password")

        if not saved_pw:
            # 기존 설정이 아예 없는 최초 등록 모드인 경우 바로 편집 활성화
            self.set_config_fields_state("normal")
            messagebox.showinfo("최초 등록 모드 🟢", "기존 설정이 존재하지 않는 최초 등록 모드입니다.\n설정 입력창과 저장 버튼이 활성화되었습니다.")
            return

        if entered_pw == saved_pw:
            self.set_config_fields_state("normal")
            messagebox.showinfo("인증 성공 🟢", "연동 비밀번호 인증 성공!\n설정 입력창과 저장 버튼이 모두 활성화되었습니다. 수정을 진행하세요.")
        else:
            messagebox.showerror("인증 실패 ❌", "연동 비밀번호가 일치하지 않습니다. 다시 한번 확인해 주세요.")

    def load_initial_data(self):
        # 저장되어 있는 모든 정보 불러오기
        company = get_config_value("company_name")
        group = get_config_value("group_name") or "지속성장"
        biz_num = get_config_value("business_number")
        phone = get_config_value("phone")
        fax = get_config_value("fax")
        address = get_config_value("address")
        is_dyn = get_config_value("is_dynamic_ip")

        self.company_entry.delete(0, tk.END)
        self.company_entry.insert(0, company)

        self.group_entry.delete(0, tk.END)
        self.group_entry.insert(0, group)

        # 직원 소속 그룹 초기화 연동 - 사훈과 연동 해제!
        # self._update_emp_group_entry(group)

        self.biz_entry.delete(0, tk.END)
        self.biz_entry.insert(0, biz_num)

        self.phone_entry.delete(0, tk.END)
        self.phone_entry.insert(0, phone)

        self.fax_entry.delete(0, tk.END)
        self.fax_entry.insert(0, fax)

        self.address_entry.delete(0, tk.END)
        self.address_entry.insert(0, address)

        # 비밀번호는 보안상 빈 칸으로 유지하여 활성화 인증을 유도합니다.
        self.password_entry.delete(0, tk.END)

        if is_dyn == "1":
            self.dynamic_ip_var.set(1)
        else:
            self.dynamic_ip_var.set(0)

        # 10분 주기 유동 IP 감시 백그라운드 엔진 가동
        self.start_ip_monitoring()

        if biz_num:
            # 기존 설정이 존재하는 경우 보안을 위해 입력 필드를 즉시 비활성화(잠금)합니다.
            self.set_config_fields_state("disabled")
            self.fetch_and_render_members()
            status_text = f"구동 중: http://localhost:8001 | 사업자번호: {biz_num} (🔒 입력 필드 잠금 상태)"
            if not TRAY_AVAILABLE:
                status_text += " (💡 'pip install pystray pillow' 시 알림영역 백그라운드 가동 활성화)"
            self.status_bar.config(text=status_text)
        else:
            self.set_config_fields_state("normal")
            status_text = "구동 중: http://localhost:8001 | 사업자 및 회사 정보를 등록해 주세요."
            if not TRAY_AVAILABLE:
                status_text += " (💡 'pip install pystray pillow' 시 백그라운드 트레이 기능 활성화)"
            self.status_bar.config(text=status_text)

    def on_toggle_dynamic_ip(self):
        is_dynamic = self.dynamic_ip_var.get()
        save_config_value("is_dynamic_ip", str(is_dynamic))
        state_str = "활성화" if is_dynamic else "비활성화"
        print(f"ℹ️ 유동 IP 실시간 감시 기능 {state_str}")

        # 유동 IP 체크 시 즉시 메인 서버에 갱신 처리
        if is_dynamic:
            company = self.company_entry.get().strip()
            biz_num = self.biz_entry.get().strip()
            if company and biz_num:
                def run_instant_reg():
                    try:
                        register_server_to_main(biz_num, company, 1)
                        print("🔄 [유동 IP 즉시 등록] 성공")
                    except Exception as e:
                        print(f"❌ [유동 IP 즉시 등록] 실패: {e}")
                threading.Thread(target=run_instant_reg, daemon=True).start()

    def start_ip_monitoring(self):
        """10분 주기로 IP 변화를 감시하고 필요시 메인 서버에 갱신하는 백그라운드 스레드"""
        def monitor():
            last_registered_ip = None
            while True:
                is_dynamic = get_config_value("is_dynamic_ip") == "1"
                if is_dynamic:
                    biz_num = get_config_value("business_number")
                    company = get_config_value("company_name")

                    if biz_num and company:
                        try:
                            current_ip = get_local_ip()
                            if current_ip != last_registered_ip:
                                print(f"🔄 [유동 IP 엔진] IP 변경 감지: {last_registered_ip} -> {current_ip}. 메인 서버 등록 요청...")
                                register_server_to_main(biz_num, company, 1)
                                last_registered_ip = current_ip
                                # Tkinter GUI 안전 스레드 연동
                                self.after(0, lambda ip=current_ip: self.status_bar.config(
                                    text=f"구동 중: http://localhost:8001 | 유동IP 자동갱신 완료 ({ip})"
                                ))
                        except Exception as e:
                            print(f"❌ [유동 IP 엔진] 메인 서버 자동 갱신 실패: {e}")

                # 10분 대기 (600초) - 10초마다 안전 체크하여 종료 대응성 유지
                for _ in range(60):
                    time.sleep(10)

        threading.Thread(target=monitor, daemon=True).start()

    def on_save_and_fetch(self):
        company = self.company_entry.get().strip()
        group = self.group_entry.get().strip() or "지속성장"
        biz_num = self.biz_entry.get().strip()
        phone = self.phone_entry.get().strip()
        fax = self.fax_entry.get().strip()
        address = self.address_entry.get().strip()
        password = self.password_entry.get().strip()
        is_dynamic = self.dynamic_ip_var.get()

        if not company:
            messagebox.showwarning("입력 에러", "회사명을 입력해 주세요.")
            return
        if not biz_num:
            messagebox.showwarning("입력 에러", "사업자 등록번호를 입력해 주세요.")
            return
        if not phone:
            messagebox.showwarning("입력 에러", "전화번호를 입력해 주세요.")
            return
        if not fax:
            messagebox.showwarning("입력 에러", "팩스번호를 입력해 주세요.")
            return
        if not address:
            messagebox.showwarning("입력 에러", "주소를 입력해 주세요.")
            return
        if not password:
            messagebox.showwarning("입력 에러", "연동 비밀번호를 입력해 주세요.")
            return

        # 메인 서버에 사내 PC IP 실시간 연동 등록 시도 (로컬 저장을 수행하기 전 검증을 필히 실행합니다)
        local_ip = get_local_ip()
        try:
            # 임시 임시 저장을 통해 register_server_to_main 이 설정을 참고할 수 있도록 함
            save_config_value("password", password)
            save_config_value("phone", phone)
            save_config_value("fax", fax)
            save_config_value("address", address)

            register_server_to_main(biz_num, company, is_dynamic)
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                detail_msg = err_data.get("detail", str(e))
            except Exception:
                detail_msg = str(e)

            # 비밀번호 매칭 오류 등 예외 발생 시 로컬 저장 프로세스 전면 차단
            messagebox.showerror("연동 등록 실패 ❌", detail_msg)
            return
        except Exception as e:
            messagebox.showwarning("네트워크 연결 실패 ⚠️", f"중앙 서버 접속 과정에서 네트워크 오류가 발생했습니다: {e}")
            return

        # 검증 통과 시에만 최종 로컬 설정 DB 영구 저장
        save_config_value("company_name", company)
        save_config_value("group_name", group)
        save_config_value("business_number", biz_num)
        save_config_value("phone", phone)
        save_config_value("fax", fax)
        save_config_value("address", address)
        save_config_value("is_dynamic_ip", str(is_dynamic))

        messagebox.showinfo("저장 및 연동 성공 🟢",
                            f"사업자 정보 저장 및 사내 PC IP ({local_ip}:8001)가 메인 서버에 안전하게 등록 및 연동 완료되었습니다!")

        # 저장 완료 후 보안을 위해 즉시 입력창 및 버튼 비활성화(잠금) 처리
        self.password_entry.delete(0, tk.END)
        self.set_config_fields_state("disabled")

        # 직원 소속 그룹 동기화 - 연동 해제!
        # self._update_emp_group_entry(group)

        self.status_bar.config(text=f"구동 중: http://localhost:8001 | 사업자번호: {biz_num} (🔒 입력 필드 잠금 상태)")
        self.fetch_and_render_members()

        # 메인 서버 연동 갱신 시 모든 직원 정보를 메인 서버에 일괄 동기화 (백그라운드 스레드)
        threading.Thread(target=sync_all_employees_to_main, daemon=True).start()

    def fetch_and_render_members(self):
        """로컬 DB (activation_keys) 로부터 직원 정보를 조회하여 트리뷰 테이블을 갱신 렌더링"""
        try:
            conn = sqlite3.connect(SUBSERVER_DB_PATH)
            c = conn.cursor()
            c.execute("""SELECT name, nickname, phone, email, division, department, position,  key, is_active, allowed_depts
                         FROM activation_keys ORDER BY created_at DESC""")
            rows = c.fetchall()
            conn.close()

            # 트리뷰 초기화
            for item in self.tree.get_children():
                self.tree.delete(item)

            self.fetched_employees = []
            for row in rows:
                name, nickname, phone, email, division, department, position, key, is_active, allowed_depts = row
                self.fetched_employees.append({
                    "name": name,                    
                    "nickname": nickname if nickname else "",
                    "phone": phone,
                    "email": email if email else "",
                    "division": division if division else "",
                    "department": department if department else "",
                    "position": position if position else "",
                    "key": key if key else "",
                    "is_active": is_active,
                    "allowed_depts": allowed_depts if allowed_depts else ""
                })

                display_key = key if key else "미발급 ❌"
                if is_active == 0:
                    display_key = "인증 중단 🚫"

                self.tree.insert("", "end", values=(
                    name,
                    nickname if nickname else "",
                    phone,
                    email if email else "",
                    division if division else "",
                    department if department else "",
                    position if position else "",
                    display_key
                ))

            biz_num = self.biz_entry.get().strip()
            self.status_bar.config(text=f"구동 중: http://localhost:8001 | 사업자번호: {biz_num} | 등록 직원 수: {len(rows)}명")
        except Exception as e:
            messagebox.showerror("직원 조회 오류", f"로컬 DB 직원 정보를 가져오지 못했습니다: {e}")

    def on_save_employee(self):
        """사내 직원의 상세 정보를 로컬 DB에 추가 또는 수정하여 저장합니다."""
        name = self.emp_name_entry.get().strip()
        nickname = self.emp_nickname_entry.get().strip()
        if nickname == self.emp_nickname_placeholder:
            nickname = ""
        phone = self.emp_phone_entry.get().strip().replace("-", "")
        email = self.emp_email_entry.get().strip()
        division = self.emp_group_entry.get().strip()

        # 중복 체크박스 상태 취합 (권한용)
        selected_depts = [dname for dname, var in self.emp_dept_vars.items() if var.get()]
        allowed_depts = ",".join(selected_depts)
        department = division # 주 부서는 그룹명으로 일단 대체하거나 별도 로직 필요 (기존 로직 유지)
        position = self.emp_pos_entry.get().strip()

        if not name:
            messagebox.showwarning("입력 에러", "직원 성함을 입력해 주세요.")
            return
        if not phone:
            messagebox.showwarning("입력 에러", "직원 전화번호를 입력해 주세요.")
            return
        if not phone.isdigit():
            messagebox.showwarning("입력 에러", "전화번호는 하이픈(-) 없이 숫자만 입력해 주세요.")
            return

        try:
            company_name = self.company_entry.get().strip() or "사내연동기업"
            company_number = get_business_number() or ""
            conn = sqlite3.connect(SUBSERVER_DB_PATH)
            c = conn.cursor()

            # 이미 동일 전화번호를 가진 직원이 있는지 확인
            c.execute("SELECT key, is_active, nickname FROM activation_keys WHERE phone = ?", (phone,))
            row = c.fetchone()

            if row:
                existing_key, existing_is_active, existing_nickname = row
                # 정보 수정
                c.execute("""UPDATE activation_keys
                             SET company_name = ?, company_number = ?, name = ?, nickname = ?, email = ?, division = ?, department = ?, position = ?, allowed_depts = ?
                             WHERE phone = ?""",
                          (company_name, company_number, name, nickname, email, division, department, position, allowed_depts, phone))
                verification_key = existing_key
                is_active = existing_is_active
                msg = f"직원 '{name}'님의 상세 정보가 수정되었습니다."
            else:
                # 신규 등록 (인증키 자동 발급)
                chars = string.ascii_uppercase + string.digits
                auto_key = "SUB-" + "".join(random.choice(chars) for _ in range(8))

                c.execute("""INSERT INTO activation_keys (company_name, company_number, name, phone, nickname, email, division, department, position, key, created_at, is_active, allowed_depts)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                          (company_name, company_number, name, phone, nickname, email, division, department, position, auto_key,
                           datetime.now().strftime("%Y-%m-%d %H:%M:%S"), allowed_depts))
                verification_key = auto_key
                is_active = 1
                msg = f"신규 직원 '{name}'님이 로컬 DB에 등록되었습니다.\n\n🔑 자동 발급 인증키: {auto_key}"

            conn.commit()
            conn.close()

            # 메인서버로 실시간 동기화 (백그라운드 스레드)
            threading.Thread(target=sync_employee_to_main, args=(phone, "upsert"), daemon=True).start()

            messagebox.showinfo("저장 완료", msg)
            self.clear_employee_inputs()
            self.fetch_and_render_members()

        except Exception as e:
            messagebox.showerror("DB 저장 오류", f"직원 정보를 기록하는 중 에러가 발생했습니다: {e}")

    def clear_employee_inputs(self):
        """직원 정보 입력창을 모두 초기화합니다."""
        self.emp_name_entry.delete(0, tk.END)
        self.emp_nickname_entry.delete(0, tk.END)
        self.emp_phone_entry.delete(0, tk.END)
        self.emp_email_entry.delete(0, tk.END)

        group_val = self.group_entry.get().strip() or "본사"
        self._update_emp_group_entry(group_val)

        self.emp_pos_entry.delete(0, tk.END)

        # 모든 부서 체크박스 초기화
        for var in self.emp_dept_vars.values():
            var.set(False)

        self.tree.selection_remove(self.tree.selection())

    def on_tree_select(self, event):
        """테이블에서 직원을 클릭하면 상단 입력 필드로 자동 로딩합니다."""
        selected = self.tree.selection()
        if not selected:
            return

        # 선택된 행의 값 추출
        item_values = self.tree.item(selected[0], "values")
        # columns: name, nickname, phone, email, division, dept, pos, key
        self.emp_name_entry.delete(0, tk.END)
        self.emp_name_entry.insert(0, item_values[0])

        self.emp_nickname_entry.delete(0, tk.END)
        self.emp_nickname_entry.insert(0, item_values[1])

        self.emp_phone_entry.delete(0, tk.END)
        self.emp_phone_entry.insert(0, item_values[2])
        self.emp_email_entry.delete(0, tk.END)
        self.emp_email_entry.insert(0, item_values[3])

        self._update_emp_group_entry(item_values[4])

        # 부서 체크박스 상태 복원
        for var in self.emp_dept_vars.values():
            var.set(False)
        dept_str = item_values[5]
        for dname in self.emp_dept_vars.keys():
            if dname in dept_str:
                self.emp_dept_vars[dname].set(True)

        self.emp_pos_entry.delete(0, tk.END)
        self.emp_pos_entry.insert(0, item_values[6])

    def on_reissue_key(self):
        """선택된 직원의 인증키를 난수로 즉시 재발행합니다."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("선택 오류", "인증키를 재발행할 직원을 선택해 주세요.")
            return

        item_values = self.tree.item(selected[0], "values")
        name = item_values[0]
        phone = item_values[2]

        if messagebox.askyesno("인증키 재발행", f"정말로 '{name}' 직원의 연동 인증키를 신규 재발행하시겠습니까?\n이전 인증키는 즉시 무효화됩니다."):
            try:
                # 8자리 대문자/숫자 난수 생성
                chars = string.ascii_uppercase + string.digits
                new_key = "SUB-" + "".join(random.choice(chars) for _ in range(8))

                conn = sqlite3.connect(SUBSERVER_DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE activation_keys SET key = ?, is_active = 1 WHERE phone = ?", (new_key, phone))
                conn.commit()
                conn.close()

                # 메인서버로 실시간 동기화 (백그라운드 스레드)
                threading.Thread(target=sync_employee_to_main, args=(phone, "upsert"), daemon=True).start()

                # 클립보드에 키 복사
                self.clipboard_clear()
                self.clipboard_append(new_key)

                messagebox.showinfo("재발행 완료", f"'{name}' 직원의 인증키가 성공적으로 재발행되었습니다!\n\n🔑 새 인증키: {new_key}\n\n* 인증키가 컴퓨터 클립보드에 자동 복사되었습니다.")
                self.fetch_and_render_members()

            except Exception as e:
                messagebox.showerror("DB 오류", f"인증키 재발행 중 오류 발생: {e}")

    def on_disable_auth(self):
        """선택된 직원의 인증 상태를 강제 중단(비활성화) 처리합니다."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("선택 오류", "인증을 해제할 직원을 선택해 주세요.")
            return

        item_values = self.tree.item(selected[0], "values")
        name = item_values[0]
        phone = item_values[2]

        if messagebox.askyesno("인증 중단", f"'{name}' 직원의 스마트폰 연동 인증을 중단(비활성화)하시겠습니까?\n중단 시 스마트폰 앱과의 사내 PC 연결 기능이 모두 차단됩니다."):
            try:
                conn = sqlite3.connect(SUBSERVER_DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE activation_keys SET is_active = 0 WHERE phone = ?", (phone,))
                conn.commit()
                conn.close()

                # 메인서버로 실시간 동기화 (백그라운드 스레드)
                threading.Thread(target=sync_employee_to_main, args=(phone, "upsert"), daemon=True).start()

                messagebox.showinfo("비활성화 성공", f"'{name}' 직원의 연동 인증이 정상적으로 해제/중단 처리되었습니다.")
                self.fetch_and_render_members()

            except Exception as e:
                messagebox.showerror("DB 오류", f"인증 중단 설정 중 오류 발생: {e}")

    def on_delete_employee(self):
        """선택된 직원을 로컬 DB에서 영구 삭제합니다."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("선택 오류", "삭제할 직원을 목록에서 선택해 주세요.")
            return

        item_values = self.tree.item(selected[0], "values")
        name = item_values[0]
        phone = item_values[2]

        if messagebox.askyesno("직원 영구 삭제", f"정말로 직원 '{name}'님을 사내 데이터베이스에서 영구 삭제하시겠습니까?\n삭제 즉시 모든 인증 및 연동 정보가 즉시 파괴됩니다."):
            try:
                # 메인서버로 실시간 동기화 (삭제 - 로컬 삭제 이전에 실행)
                threading.Thread(target=sync_employee_to_main, args=(phone, "delete"), daemon=True).start()

                conn = sqlite3.connect(SUBSERVER_DB_PATH)
                c = conn.cursor()
                c.execute("DELETE FROM activation_keys WHERE phone = ?", (phone,))
                conn.commit()
                conn.close()

                messagebox.showinfo("삭제 완료", f"직원 '{name}'님이 로컬 데이터베이스에서 성공적으로 영구 삭제되었습니다.")
                self.clear_employee_inputs()
                self.fetch_and_render_members()

            except Exception as e:
                messagebox.showerror("DB 오류", f"직원 정보 삭제 중 오류 발생: {e}")

    def on_copy_key(self):
        """선택된 직원의 발급 인증키를 간편 클립보드 복사합니다."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("선택 오류", "인증키를 복사할 직원을 선택해 주세요.")
            return

        item_values = self.tree.item(selected[0], "values")
        name = item_values[0]
        key = item_values[7]

        if "❌" in key or "🚫" in key or not key:
            messagebox.showwarning("복사 불가", "해당 직원은 유효하게 발급된 인증키가 없습니다.")
            return

        self.clipboard_clear()
        self.clipboard_append(key)
        messagebox.showinfo("복사 완료", f"직원 '{name}'님의 인증키 ({key}) 가 클립보드에 복사되었습니다.\n직원에게 안전하게 전달하여 연동하도록 안내하세요!")

if __name__ == "__main__":
    ensure_activation_keys_schema()
    app = SubServerGUI()
    app.mainloop()
