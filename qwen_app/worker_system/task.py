"""
Worker 任务模块 — 线程安全的异步任务实现

本模块提供基于 QRunnable 的轻量级异步任务封装。
对于完整功能（思考标签解析、工具调用循环、插件支持），
请直接使用 worker.WorkerThread。
"""
from PyQt5.QtCore import QRunnable, pyqtSignal, QObject
import threading


class WorkerSignals(QObject):
    """Worker 信号容器"""
    chunk_received = pyqtSignal(str, bool)   # (内容, 是否思考中)
    finished = pyqtSignal()                  # 任务完成
    error = pyqtSignal(str)                  # 错误消息


class AsyncWorker(QRunnable):
    """异步 Worker 任务 — 在后台线程执行简单 AI 请求

    注意：此类的精简版本，仅支持基础流式响应。
    如需思考标签解析、工具调用循环、插件集成等完整功能，
    请使用 worker.WorkerThread（已增强线程安全性）。
    """

    def __init__(self, client, model_id, messages, timeout=60):
        super().__init__()
        self.client = client
        self.model_id = model_id
        self.messages = messages
        self.signals = WorkerSignals()
        self._stop_event = threading.Event()  # 线程安全停止信号
        self._timeout = timeout
        self._completion = None
        self.setAutoDelete(True)              # 任务完成后自动清理

    def run(self):
        """主执行逻辑（在后台线程运行）"""
        try:
            self._completion = self.client.chat.completions.create(
                model=self.model_id,
                messages=self.messages,
                stream=True,
                timeout=self._timeout
            )

            for chunk in self._completion:
                if self._stop_event.is_set():  # 检查停止信号
                    break

                if chunk.choices and chunk.choices[0].delta.content:
                    self.signals.chunk_received.emit(
                        chunk.choices[0].delta.content,
                        False  # 非思考状态
                    )

        except Exception as e:
            self.signals.error.emit(f"请求失败: {str(e)}")
        finally:
            # 安全关闭流式连接
            if self._completion is not None:
                try:
                    if hasattr(self._completion, 'close'):
                        self._completion.close()
                except Exception:
                    pass
            self.signals.finished.emit()

    def stop(self):
        """安全停止任务"""
        self._stop_event.set()

    def is_stopped(self):
        """查询是否已收到停止信号"""
        return self._stop_event.is_set()
