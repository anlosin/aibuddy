# -*- coding: utf-8 -*-
"""手动探针：验证打包后的 ``dist/qwen/qwen.exe`` 真能**启动出窗口**。

为什么必须数窗口而不是看日志：
    PyInstaller 打包最常见的失败模式是「进程活着、无异常、日志正常，但界面不出来」
    （与源码态 QQmlApplicationEngine 被 GC 是同一类症状）。日志会说「已启动」，
    所以判据只能是**枚举该进程的可见顶层窗口**。

同时校验打包最关键的路径改造：
    · exe 同级是否自动释放了 plugins/      （外部插件优先，保留热重载）
    · 数据是否落到 exe 同级 data/          （便携，而不是 _internal 临时目录）

用法：
    .venv\\Scripts\\python.exe tests/manual_exe_launch_probe.py
返回码：0 = 出现窗口且路径正确；1 = 失败（并打印 app.log 尾巴便于定位）。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "qwen")
EXE = os.path.join(DIST, "qwen.exe")

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.EnumWindows.restype = wintypes.BOOL
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of_pid(pid):
    """返回该进程的所有「可见且带标题」的顶层窗口 [(hwnd, title)]。"""
    found = []

    def _cb(hwnd, _lparam):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found


def _tail(path, n=25):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "\n".join(fh.read().splitlines()[-n:])
    except Exception as e:
        return f"(读取失败: {e})"


def main():
    if not os.path.isfile(EXE):
        print(f"[probe] 未找到 {EXE}\n        先构建：python -m PyInstaller qwen.spec --noconfirm")
        return 1

    print(f"[probe] 启动 {EXE}")
    log = os.path.join(DIST, "data", "logs", "app.log")
    before = os.path.getsize(log) if os.path.isfile(log) else 0

    proc = subprocess.Popen([EXE], cwd=DIST)
    wins = []
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"[probe] !! 进程提前退出，exitcode={proc.returncode}")
                break
            wins = windows_of_pid(proc.pid)
            if wins:
                break
            time.sleep(0.7)

        print(f"[probe] 进程 pid={proc.pid}, 可见有标题窗口数 = {len(wins)}")
        for hwnd, title in wins:
            print(f"          - hwnd={hwnd}  title={title!r}")

        # ── 打包路径改造校验 ──
        plugins_dir = os.path.join(DIST, "plugins")
        data_dir = os.path.join(DIST, "data")
        py_files = []
        if os.path.isdir(plugins_dir):
            py_files = [f for f in os.listdir(plugins_dir) if f.endswith(".py")]
        print(f"[probe] exe 同级 plugins/ 存在: {os.path.isdir(plugins_dir)}"
              f"  插件数 = {len(py_files)}")
        print(f"[probe] exe 同级 data/   存在: {os.path.isdir(data_dir)}"
              f"  内容 = {sorted(os.listdir(data_dir))[:8] if os.path.isdir(data_dir) else []}")
        inner_data = os.path.join(DIST, "_internal", "data")
        print(f"[probe] 误写 _internal/data ? {os.path.isdir(inner_data)}  （应为 False）")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    ok = bool(wins) and os.path.isdir(os.path.join(DIST, "plugins")) \
        and os.path.isdir(os.path.join(DIST, "data"))
    if not wins:
        print("[probe] 没有窗口 —— app.log 尾巴：")
        print(_tail(log))
        if os.path.isfile(log) and os.path.getsize(log) == before:
            print("        (app.log 没有新增内容 —— 可能崩在 stdout 兜底之前)")
    print("[probe] 结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
