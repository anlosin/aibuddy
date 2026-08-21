"""Compressor — 对话智能压缩与摘要模块

提供 AI 驱动的智能摘要、历史清理功能，大幅减少上下文占用。
核心思路：使用 AI 模型将长篇对话压缩成结构化关键信息总结。
"""
from typing import List, Dict, Tuple


class ConversationCompressor:
    """对话压缩器 - 智能分析并压缩对话历史"""

    # 策略阈值（可根据实际 token 限制调整）
    MAX_HISTORY_ITEMS = 100  # 超过此数量后触发自动压缩
    COMPRESS_THRESHOLD = 20  # 至少保留多少条消息在展开摘要中

    def __init__(self, api_key: str, base_url: str, model_id: str, proxy: str = ""):
        """
        初始化压缩器

        Args:
            api_key: OpenAI 兼容 API 密钥
            base_url: API 基础 URL
            model_id: 用于生成摘要的模型 ID
            proxy: 代理地址（为空=不走代理，仅作用于模型连接）
        """
        try:
            from .config import make_openai_client
            self.client = make_openai_client(api_key, base_url, proxy)
        except ImportError:
            raise ImportError("需要安装 openai 库：pip install openai")

        self.model_id = model_id
        self._is_compressing = False

    @property
    def is_compressing(self) -> bool:
        """是否正在压缩中"""
        return self._is_compressing

    def compress(self, history: List[Dict]) -> Tuple[List[Dict], Dict]:
        """
        压缩对话历史，返回压缩后的历史记录和摘要元数据
        
        Args:
            history: 原始对话历史列表，格式为 [{"role": "user/assistant", "content": "..."}, ...]
        
        Returns:
            (compressed_history, metadata)
                - compressed_history: 压缩后的历史记录
                - metadata: 包含摘要信息的字典
        """
        if len(history) <= self.COMPRESS_THRESHOLD:
            # 未达到压缩阈值，直接返回原始数据
            return list(history), {"original_count": len(history), "compressed": False}

        # 检查是否存在摘要标记
        has_summary = any(h.get("__is_summary__", False) for h in history)

        if has_summary:
            # 已存在摘要，只需追加最近消息并更新统计
            return self._update_existing_summary(history)
        else:
            # 首次压缩，生成完整摘要
            return self._generate_first_summary(history)

    def _update_existing_summary(self, history: List[Dict]) -> Tuple[List[Dict], Dict]:
        """更新已有摘要，只保留近期交互"""
        original_count = len(history)

        # 分离出摘要部分和近期消息
        summary_lines = [h for h in history if h.get("__is_summary__", False)]
        recent_messages = [h for h in history if not h.get("__is_summary__", False)]

        # 只保留最近的 COMPRESS_THRESHOLD 条消息
        recent_to_keep = recent_messages[-self.COMPRESS_THRESHOLD:]

        # 合并结果
        compressed = list(summary_lines) + recent_to_keep

        # 更新摘要统计
        new_metadata = {
            "__is_summary__": True,
            "summary_content": summary_lines[0]["content"] if summary_lines else "",
            "total_original": original_count,
            "recent_kept": len(recent_to_keep),
            "current_size": len(compressed),
        }

        return compressed, new_metadata

    def _generate_first_summary(self, history: List[Dict]) -> Tuple[List[Dict], Dict]:
        """
        首次生成完整对话摘要

        通过 AI 模型生成结构化的对话摘要，提取关键信息并压缩存储
        """
        total_messages = len(history)

        # 构建系统提示词，指导 AI 进行结构化摘要
        system_prompt = (
            "你是一个专业的对话摘要专家。\n"
            "请将对话压缩为结构化格式，包含：\n"
            "1. 用户目标/需求\n"
            "2. 讨论要点\n"
            "3. 解决方案/结论\n"
            "4. 待解决问题（如果有）\n\n"
            "要求：\n"
            "- 简洁清晰，控制在 500 字以内\n"
            "- 保持原意不变\n"
            "- 使用 bullet points 分点陈述\n"
            "- 不要丢失关键信息\n\n"
            "输出格式示例：\n"
            "=== 对话摘要 ===\n\n"
            "🎯 用户目标：...\n\n"
            "💡 讨论要点：\n"
            "  • ...\n"
            "  • ...\n\n"
            "✅ 解决方案：...\n\n"
            "⏳ 待处理：..."
        )

        # 准备发送给 API 的消息内容
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # 选取最近 N 条消息用于摘要（避免 token 超限）
        recent_samples = history[-80:]  # 最多取 80 条消息进行分析
        messages.extend([{"role": m["role"], "content": m["content"]} for m in recent_samples])

        messages.append({
            "role": "user",
            "content": f"\n请对以上共{total_messages}条消息的对话进行结构化摘要。确保覆盖所有重要决策和结论。"
        })

        try:
            # 调用 API 生成摘要
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                temperature=0.3,
                max_tokens=600
            )

            summary_text = response.choices[0].message.content.strip()

            # 构建压缩后的历史记录
            compressed = [{
                "role": "system",
                "content": summary_text,
                "__is_summary__": True,
            }]

            # 添加最近的少量原始消息以保持上下文连贯性
            recent_context = history[-self.COMPRESS_THRESHOLD:]
            compressed.extend(recent_context)

            metadata = {
                "original_count": total_messages,
                "compressed": True,
                "compress_time": self._get_timestamp(),
                "summary_truncated": True,
            }

            return compressed, metadata

        except Exception as e:
            print(f"警告：生成对话摘要失败 - {e}")
            # 降级方案：保留最近的消息作为压缩结果
            fallback_compressed = history[-50:]
            fallback_metadata = {
                "original_count": total_messages,
                "compressed": False,
                "error": str(e),
            }
            return fallback_compressed, fallback_metadata

    def get_status(self, history: List[Dict]) -> Dict:
        """
        获取对话状态信息

        Args:
            history: 当前对话历史

        Returns:
            包含压缩状态的字典
        """
        has_summary = any(h.get("__is_summary__", False) for h in history)
        original_count = len(history)

        recent_count = len([h for h in history if not h.get("__is_summary__", False)])

        status = {
            "history_size": len(history),
            "original_message_count": original_count,
            "has_summary": has_summary,
            "needs_compression": len(history) > self.MAX_HISTORY_ITEMS,
            "recommended_action": None,
        }

        if has_summary:
            summary_info = next((h for h in history if h.get("__is_summary__", False)), {})
            preview = summary_info.get("content", "")[:100]
            status["summary_preview"] = preview + "..." if len(preview) == 100 else preview

        if status["needs_compression"]:
            status["recommended_action"] = "recommend_compress"
        elif has_summary and recent_count < self.COMPRESS_THRESHOLD:
            status["recommended_action"] = "ready_for_new_cycle"

        return status

    def delete_summaries(self, history: List[Dict]) -> List[Dict]:
        """删除所有摘要，恢复原始历史"""
        return [h for h in history if not h.get("__is_summary__", False)]

    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def cancel_operation(self):
        """取消当前操作"""
        self._is_compressing = False
