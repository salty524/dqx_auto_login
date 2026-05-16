import hashlib
import logging
import glob
import sys
import time
import subprocess
import os
import platform
import getpass
import winreg
import requests
import ctypes
import json
import pyotp
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import messagebox
import webbrowser
import pwinput
import win32gui
import win32process
import win32con
import psutil
from datetime import datetime
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin

# ==========================================
# 0. ログ設定
# ==========================================

def setup_logging():
    # 同フォルダにログファイルを作成。起動ごとに前回分を削除して1件だけ残す
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    for old in glob.glob(os.path.join(base_dir, "*.log")):
        try:
            os.remove(old)
        except OSError:
            pass

    log_path = os.path.join(base_dir, datetime.now().strftime("%Y%m%d_%H%M%S.log"))

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    logging.getLogger("urllib3").setLevel(logging.WARNING)

    return logging.getLogger(__name__)

log = setup_logging()

# ==========================================
# 1. アップデート確認
# ==========================================

CURRENT_VERSION = "v1.0.0"
GIST_ID = "fd82d5eed4576f48ae098a1104058dff"

def check_for_update():
    try:
        # API経由で最新のraw URLを取得してバージョン情報を読む
        api_res = requests.get(f"https://api.github.com/gists/{GIST_ID}", timeout=5)
        raw_url = api_res.json()["files"]["version.json"]["raw_url"]
        data = requests.get(raw_url, timeout=5).json()
        latest = data.get("latest", "")
        message = data.get("message", "")
        url = data.get("url", "")

        if not latest or latest == CURRENT_VERSION:
            return

        log.info(f"アップデートがあります: {CURRENT_VERSION} → {latest}")
        if message:
            log.info(f"  更新内容: {message}")
        if url:
            log.info(f"  ダウンロード: {url}")

        lines = [f"新しいバージョンがあります: {CURRENT_VERSION} → {latest}"]
        if message:
            lines.append(f"更新内容: {message}")
        if url:
            lines.append(f"\nダウンロードページを開きますか？\n（「いいえ」でそのまま起動します）")

        root = tk.Tk()
        root.withdraw()
        if url and messagebox.askyesno("アップデート通知", "\n".join(lines)):
            webbrowser.open(url)
            log.info("ダウンロードページを開きました。ランチャーを終了します。")
            root.destroy()
            raise SystemExit
        root.destroy()
    except SystemExit:
        raise
    except Exception:
        log.debug("バージョン確認に失敗しました（スキップ）", exc_info=True)

# ==========================================
# 2. 設定管理
# ==========================================

CONFIG_FILE = "config.json"

# DQX各バージョンのインストーラGUID（Ver1.0〜7.0）
_EXPAC_GUIDS = [
    "{300DCC8E-BE61-4FB5-B9D8-FDA19E3AAA38}",  # 1.0
    "{4FD779A0-9CAE-4A36-A33E-EB01DA36537E}",  # 2.0
    "{1D79B85A-17B7-40E0-94ED-791572CB082E}",  # 3.0
    "{B6A99A93-03DB-49EB-8F04-78AA22D571EC}",  # 4.0
    "{5B536B7A-9189-4908-AF6D-2702E23C3C67}",  # 5.0
    "{D6C2F5CC-F6F9-45BF-B83B-B28825E74855}",  # 6.0
    "{4FC73F71-D454-409C-8ADC-85AC0E10F35F}",  # 7.0
]
_REGISTRY_BASE = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"

def find_game_exe_path() -> str | None:
    # レジストリのDisplayIconからゲームのexeパスを取得する
    for guid in _EXPAC_GUIDS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{_REGISTRY_BASE}\\{guid}") as key:
                icon_file, _ = winreg.QueryValueEx(key, "DisplayIcon")
                exe_path  = icon_file[:icon_file.rfind(',')]   # ",0" を除去
                game_root = os.path.dirname(os.path.dirname(exe_path))
                candidate = os.path.join(game_root, "Game", "DQXGame.exe")
                if os.path.exists(candidate):
                    return candidate
        except OSError:
            continue
    return None

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config(config: dict):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def input_password(prompt: str) -> str:
    return pwinput.pwinput(prompt=prompt, mask='*')

def _setup_otp(config: dict) -> dict:
    answer = input("ワンタイムパスワードの自動入力を設定しますか？ (1=する / 0=しない): ").strip()
    if answer == "1":
        secret = input("ワンタイムパスワードのシークレットキーを入力してください: ").strip()
        if secret:
            config["otp_auto"] = True
            config["otp_secret"] = secret
            log.info("ワンタイムパスワード: 自動入力に設定しました。")
        else:
            log.info("シークレットキーが空欄のため手動入力に設定します。")
            config["otp_auto"] = False
            config["otp_secret"] = ""
    else:
        config["otp_auto"] = False
        config["otp_secret"] = ""
        log.info("ワンタイムパスワード: 手動入力に設定しました。")
    return config

def setup_config(config: dict) -> dict:
    changed = False
    if not config.get("user_id"):
        config["user_id"] = input("SQEX IDを入力してください: ")
        changed = True
    if not config.get("password"):
        config["password"] = input_password("パスワードを入力してください: ")
        changed = True
    if "otp_auto" not in config:
        _setup_otp(config)
        changed = True
    if "check_update" not in config:
        config["check_update"] = True
        changed = True
    if not config.get("game_exe_path"):
        exe_path = find_game_exe_path()
        if exe_path:
            log.info(f"ゲームの実行ファイルを検出しました: {exe_path}")
            config["game_exe_path"] = exe_path
        else:
            log.warning("ゲームの実行ファイルが見つかりませんでした。")
            config["game_exe_path"] = input("DQXGame.exe のフルパスを入力してください: ").strip()
        changed = True
    if changed:
        save_config(config)
    return config

# ==========================================
# 3. 認証
# ==========================================

session = requests.Session()

def make_computer_id() -> str:
    os_info = f"Microsoft Windows NT {platform.version()}"
    raw_id = platform.node() + getpass.getuser() + os_info + str(os.cpu_count())
    sha1 = hashlib.sha1(raw_id.encode('utf-16le')).digest()
    b = bytearray(5)
    b[1:5] = sha1[0:4]
    b[0] = (-(sum(b[1:5]))) & 0xFF
    return b.hex().lower()

def do_login(config: dict):
    login_url = "https://dqx-login.square-enix.com/oauth/sp/sso/dqxwin/login?client_id=dqx_win&redirect_uri=https%3a%2f%2fdqx%2dlogin%2esquare%2denix%2ecom%2f&response_type=code"
    session.headers.update({"User-Agent": f"User-Agent: SQEXAuthor/2.0.0(Windows 6.2; ja-jp; {make_computer_id()})"})

    log.info("サーバーに接続中...")
    res = session.get(login_url)

    token = config.get("account_token")
    log.info("ログイン処理を開始します...")
    res = session.post(login_url, data={"dqxmode": "2" if token else "1", "id": token if token else ""}, allow_redirects=True)

    for _ in range(10):
        soup = BeautifulSoup(res.text, 'html.parser')

        auth = soup.find('x-sqexauth')
        if isinstance(auth, Tag) and auth.get('sid'):
            new_token = auth.get('id')
            if new_token and config.get("account_token") != new_token:
                config["account_token"] = str(new_token)
                save_config(config)
            log.info("ログインに成功しました。ゲームを起動します...")
            return str(auth.get('sid'))

        form = soup.find('form', {'name': 'mainForm'})
        if not isinstance(form, Tag):
            log.error(f"ログインフォームが見つかりませんでした。URL: {res.url}")
            return None

        action_url = urljoin(res.url, str(form.get('action') or ""))
        payload = {
            str(n.get('name')): n.get('value', '')
            for n in form.find_all('input') # type: ignore
            if n.get('name') # type: ignore
        }

        if 'sqexid' in payload:
            payload['sqexid'] = config["user_id"]
        if 'password' in payload:
            payload['password'] = config["password"]

        if 'otppw' in payload:
            if "otp_auto" not in config:
                log.info("ワンタイムパスワードが要求されました。設定を行います。")
                _setup_otp(config)
                save_config(config)

            if config.get("otp_auto") and config.get("otp_secret"):
                otp = pyotp.TOTP(config["otp_secret"]).now()
                log.info(f"ワンタイムパスワードを送信します ({otp})")
                payload['otppw'] = otp
            else:
                otp = input("ワンタイムパスワードを入力してください: ").strip()
                if not otp:
                    log.error("エラー：ワンタイムパスワードが入力されませんでした。")
                    return None
                payload['otppw'] = otp

        res = session.post(action_url, data=payload, allow_redirects=True)
        time.sleep(1)

    log.error("ログイン試行回数の上限に達しました。")
    return None

# ==========================================
# 4. プレイヤーリストXML
# ==========================================

# ファイル名難読化用テーブル
_DIGIT_MAP = "&@#+(_-)]$"
_UPPER_MAP = [0x03,0x05,0x14,0x17,0x08,0x18,0x06,0x07,0x01,0x12,0x02,0x09,0x0A,0x0C,0x19,0x0D,0x04,0x0F,0x15,0x0E,0x10,0x00,0x11,0x0B,0x16,0x13]
_LOWER_MAP = [0x12,0x15,0x04,0x17,0x0B,0x19,0x0D,0x0E,0x03,0x11,0x0C,0x10,0x14,0x05,0x07,0x0F,0x08,0x06,0x01,0x09,0x02,0x0A,0x16,0x13,0x00,0x18]

# XORキー生成用CRC32（初期値0・MSBファースト・最終XORなし）
_CRC32_POLY = 0x04C11DB7
_CRC32_TABLE: list[int] = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ _CRC32_POLY) & 0xFFFFFFFF if (_c & 0x80000000) else (_c << 1) & 0xFFFFFFFF
    _CRC32_TABLE.append(_c)

def _custom_crc32(data: bytes) -> bytes:
    crc = 0
    for b in data:
        crc = ((crc << 8) ^ _CRC32_TABLE[((crc >> 24) ^ b) & 0xFF]) & 0xFFFFFFFF
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF, (crc >> 16) & 0xFF, (crc >> 24) & 0xFF])

def _obfuscate_filename(filename: str, seed: int = 0) -> str:
    checksum = seed & 0xFF
    result = []
    for ch in filename:
        c = ch
        if '0' <= ch <= '9':
            idx = (checksum + (ord(ch) - ord('0'))) % 10
            c = _DIGIT_MAP[idx]
        elif 'A' <= ch <= 'Z':
            idx = (checksum + (ord(ch) - ord('A'))) % 26
            c = chr(ord('A') + _UPPER_MAP[idx])
        elif 'a' <= ch <= 'z':
            idx = (checksum + (ord(ch) - ord('a'))) % 26
            c = chr(ord('a') + _LOWER_MAP[idx])
        elif ch == '.':
            c = '!'
        elif ch == '*':
            c = '~'
        result.append(c)
        checksum = (checksum + ord(ch)) & 0xFF
    return ''.join(result)

def _xor_deobfuscate(data: bytes, key: bytes) -> bytes:
    result = bytearray(len(data))
    klen = len(key)
    for i, b in enumerate(data):
        k = key[i % klen]
        result[i] = (b ^ k) if (b != 0x00 and b != k) else b
    return bytes(result)

def _find_dqx_save_dir() -> str | None:
    # OneDriveリダイレクトやフォルダ名の表記揺れに対応して候補を順に探す
    home = os.path.expanduser("~")
    dqx_names = ["DRAGON QUEST X", "Dragon Quest X"]
    roots = [
        os.path.join(home, "Documents", "My Games"),
        os.path.join(home, "OneDrive", "Documents", "My Games"),
        os.path.join(home, "OneDrive", "ドキュメント", "My Games"),
        os.path.join(home, "ドキュメント", "My Games"),
    ]
    for root in roots:
        for name in dqx_names:
            path = os.path.join(root, name)
            if os.path.isdir(path):
                return path
    return None

def read_player_number(account_token: str, config: dict) -> int | None:
    # 保存済みならXMLを読まずそのまま返す
    if config.get("player_number") is not None:
        return int(config["player_number"])

    save_dir = _find_dqx_save_dir()
    if save_dir is None:
        log.warning("DQXのセーブデータフォルダが見つかりません。")
        return None

    xml_path = os.path.join(save_dir, _obfuscate_filename("dqxPlayerList.xml", seed=0x11))
    log.debug(f"プレイヤーリストのパス: {xml_path}")

    if not os.path.exists(xml_path):
        log.warning(f"dqxPlayerList.xml が見つかりません: {xml_path}")
        return None

    key = _custom_crc32((getpass.getuser() + "\0").encode("ascii"))
    with open(xml_path, "rb") as f:
        raw = f.read()

    xml_str = _xor_deobfuscate(raw, key).decode("utf-8", errors="replace")

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        log.error(f"dqxPlayerList.xml の解析に失敗しました: {e}")
        return None

    players = [
        {"number": int(p.get("Number", 0)), "token": p.get("Token", "")}
        for p in root.iter("Player")
        if p.get("Number") and p.get("Token")
    ]

    if not players:
        log.warning("dqxPlayerList.xml にプレイヤーが見つかりませんでした。")
        return None

    if len(players) == 1:
        number = players[0]["number"]
        log.info(f"プレイヤー番号 {number} を取得しました。")
        config["player_number"] = number
        save_config(config)
        return number

    log.info("複数のプレイヤーが登録されています。使用するプレイヤーを選択してください：")
    for p in players:
        marker = " ← このアカウント" if p["token"] == account_token else ""
        log.info(f"  {p['number']}: プレイヤー {p['number']}{marker}")

    valid = {str(p["number"]) for p in players}
    while True:
        choice = input(f"番号を入力してください ({'/'.join(sorted(valid))}): ").strip()
        if choice in valid:
            number = int(choice)
            config["player_number"] = number
            save_config(config)
            log.info(f"プレイヤー番号 {number} を選択しました。（設定を保存しました）")
            return number
        log.warning(f"無効な入力です。{'/'.join(sorted(valid))} のいずれかを入力してください。")

# ==========================================
# 5. ゲーム起動
# ==========================================

def csharp_mod(a: int, b: int) -> int:
    return a - (int(a / b) * b)

def encode_session_id(sid: str) -> str:
    t = str(int(time.time() / 60))
    s = (f"DQUEST10{sid}" + "\0" * 64)[:64]
    h = hashlib.md5(f"{t}DraqonQuestX".encode()).digest()
    o = bytearray(64)
    for i in range(64):
        o[i] = csharp_mod(ord(s[i]) + (h[i % 16] - 48), 78) + 48
    return o.decode('utf-8')

def launch_game(sid: str, config: dict) -> tuple[subprocess.Popen, bool]:
    exe_path = config["game_exe_path"]
    game_dir = os.path.dirname(exe_path)

    winmm = ctypes.WinDLL('winmm')
    tb = "0000" + str((winmm.timeGetTime() & 0xFFFFFFFF) >> 1)
    token = "".join(chr(ord(tb[i]) ^ ord("SqEx"[i % 4])) for i in range(len(tb)))

    args = [
        exe_path,
        f"-StartupToken={token}",
        f"-SessionID={encode_session_id(sid)}",
        "-USE_APARTMENTTHREADED"
    ]

    player_number = read_player_number(config.get("account_token", ""), config)
    if player_number is not None:
        args.insert(-1, f"-PlayerNumber={player_number}")
        log.info(f"プレイヤー番号 {player_number} で起動します")
    else:
        log.warning("プレイヤー番号が見つからないため省略して起動します")

    log.debug(f"起動コマンド: {' '.join(args)}")
    return subprocess.Popen(args, cwd=game_dir, shell=False), player_number is not None

# ==========================================
# 6. ウィンドウ制御（ゲストモード時のみ使用）
# ==========================================

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

def _get_process_tree_pids(root_pid: int) -> set[int]:
    pids: set[int] = {root_pid}
    try:
        for child in psutil.Process(root_pid).children(recursive=True):
            pids.add(child.pid)
    except psutil.NoSuchProcess:
        pass
    return pids

def _find_game_window(pids: set[int]) -> int | None:
    found: list[int] = []
    def _cb(hwnd: int, _) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if not win32gui.GetWindowText(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        if pid not in pids:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] > 200 and rect[3] - rect[1] > 200:
            found.append(hwnd)
        return True
    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None

def _do_resize(hwnd: int, target_w: int, target_h: int) -> tuple[int, int]:
    rect = win32gui.GetWindowRect(hwnd)
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, target_w, target_h, win32con.SWP_NOMOVE | win32con.SWP_NOZORDER)
    rect2 = win32gui.GetWindowRect(hwnd)
    actual_w, actual_h = rect2[2] - rect2[0], rect2[3] - rect2[1]
    if actual_w != target_w or actual_h != target_h:
        win32gui.MoveWindow(hwnd, rect[0], rect[1], target_w, target_h, True)
        rect3 = win32gui.GetWindowRect(hwnd)
        actual_w, actual_h = rect3[2] - rect3[0], rect3[3] - rect3[1]
    return actual_w, actual_h

def _do_move(hwnd: int, x: int, y: int):
    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, w, h, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)

def wait_and_resize_window(proc: subprocess.Popen, config: dict):
    saved_w = config.get("window_w")
    saved_h = config.get("window_h")
    saved_x = config.get("window_x")
    saved_y = config.get("window_y")
    has_saved_size = saved_w is not None and saved_h is not None

    timeout_sec   = 120
    stable_count  = 2
    poll_interval = 0.2

    if has_saved_size:
        log.info(f"ゲームウィンドウを待機中... (目標サイズ: {saved_w}x{saved_h} / 位置: {saved_x},{saved_y})")
    else:
        log.info("ゲームウィンドウを待機中... (初回起動: 手動でサイズを調整してください。終了時に保存されます)")

    prev_size = (-1, -1)
    stable    = 0
    elapsed   = 0.0

    while elapsed < timeout_sec:
        if proc.poll() is not None:
            log.warning("ゲームプロセスが予期せず終了しました。")
            return

        pids = _get_process_tree_pids(proc.pid)
        hwnd = _find_game_window(pids)

        if hwnd:
            rect  = win32gui.GetWindowRect(hwnd)
            cur_w = rect[2] - rect[0]
            cur_h = rect[3] - rect[1]
            title = win32gui.GetWindowText(hwnd)

            if (cur_w, cur_h) == prev_size:
                stable += 1
                log.debug(f"[{title}] {cur_w}x{cur_h} – 安定 {stable}/{stable_count}")
            else:
                stable    = 0
                prev_size = (cur_w, cur_h)
                log.debug(f"[{title}] {cur_w}x{cur_h} – サイズ変化を検出、カウントリセット")

            if stable >= stable_count:
                if has_saved_size:
                    actual_w, actual_h = _do_resize(hwnd, int(saved_w), int(saved_h))
                    log.info(f"リサイズ{'成功' if (actual_w, actual_h) == (int(saved_w), int(saved_h)) else '一部完了'}: {actual_w}x{actual_h}")
                    if saved_x is not None and saved_y is not None:
                        _do_move(hwnd, int(saved_x), int(saved_y))
                        log.info(f"保存済みの位置に移動しました: ({saved_x}, {saved_y})")
                else:
                    log.info("保存済みのサイズがありません。手動でリサイズしてください。終了時にサイズと位置が保存されます。")

                _monitor_until_close(hwnd, proc, config)
                return
        else:
            if not elapsed:
                log.info("ウィンドウの表示を待機中...")

        time.sleep(poll_interval)
        elapsed += poll_interval

    log.warning("タイムアウト: ウィンドウのリサイズをスキップしました。")

def _monitor_until_close(hwnd: int, proc: subprocess.Popen, config: dict):
    poll_interval = 2.0
    rect = win32gui.GetWindowRect(hwnd)
    last_x, last_y = rect[0], rect[1]
    last_w, last_h = rect[2] - rect[0], rect[3] - rect[1]

    while True:
        if proc.poll() is not None:
            break
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            break
        rect = win32gui.GetWindowRect(hwnd)
        last_x, last_y = rect[0], rect[1]
        last_w, last_h = rect[2] - rect[0], rect[3] - rect[1]
        time.sleep(poll_interval)

    config["window_w"] = last_w
    config["window_h"] = last_h
    config["window_x"] = last_x
    config["window_y"] = last_y
    save_config(config)
    log.info(f"ゲームが終了しました。ウィンドウ情報を保存しました: {last_w}x{last_h} / 位置({last_x}, {last_y})")

# ==========================================
# Main
# ==========================================

if __name__ == "__main__":
    try:
        conf = setup_config(load_config())
        if conf.get("check_update", True):
            check_for_update()

        MAX_RETRIES = 3
        for attempt in range(1, MAX_RETRIES + 1):
            session_id = do_login(conf)
            if session_id:
                break

            log.warning(f"認証に失敗しました。({attempt}/{MAX_RETRIES})")
            if attempt == MAX_RETRIES:
                input("リトライ上限に達しました。Enterキーで終了します。")
                raise SystemExit

            answer = input("ID・パスワードを再入力しますか？ (1=する / 0=終了): ").strip()
            if answer != "1":
                input("Enterキーで終了します。")
                raise SystemExit

            conf["user_id"] = input("SQEX IDを入力してください: ").strip()
            conf["password"] = input_password("パスワードを入力してください: ")
            save_config(conf)

        game_proc, has_player_number = launch_game(session_id, conf)
        if not has_player_number:
            wait_and_resize_window(game_proc, conf)

    except SystemExit:
        raise
    except Exception:
        log.exception("予期しないエラーが発生しました。")
        input("Enterキーで終了します。")