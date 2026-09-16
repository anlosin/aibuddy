# -*- coding: utf-8 -*-
"""Day 20.6.12 护栏：API Key 不再明文落盘（P0-SEC-7）

来源：Day 20.6.10 审计 → Day 20.6.11 复核（audit_verification.md P0-SEC-7）。
事实：config.py 的 save_config 直接 json.dump，api_key 明文进
      data/model_config.json。而项目**本来就有** plugins/_secret_store
      （keyring + 内存降级，ssh/sql 密码均已接入）—— API Key 是唯一漏网项。

修复：在 load_config / save_config 两个**唯一读写入口**上集中处理：
      · 写盘前 _sanitize_api_keys —— api_key 存凭据库、磁盘留空
      · 读盘后 _hydrate_api_keys —— 从凭据库回填到内存字典
      因此调用方（chat_window / settings_dialog / scheduler / _dialog_host）
      完全不需要改，拿到的字典里 api_key 依旧可用。

**降级红线（本文件最重要的一条）**：
      只有 keyring 真实可用时才脱敏磁盘。keyring 不可用时必须保留明文 ——
      否则会出现「磁盘清了、keyring 没存上」的用户 Key 永久丢失。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from qwen_app import config          # noqa: E402
from plugins import _secret_store    # noqa: E402


class TestApiKeySecretStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="apikey_")
        self._orig_path = config.CONFIG_PATH
        config.CONFIG_PATH = os.path.join(self.tmp, "model_config.json")
        config._KEY_CACHE.clear()

        # 用一个假 keyring（内存 dict）接管读写，并声明"可用"
        self.stored = {}
        self._patchers = [
            mock.patch.object(_secret_store, "is_available",
                              return_value="FakeKeyring.Backend"),
            mock.patch.object(_secret_store, "set_secret",
                              side_effect=lambda s, n, p:
                              (self.stored.__setitem__((s, n), p), True)[1]),
            mock.patch.object(_secret_store, "get_secret",
                              side_effect=lambda s, n: self.stored.get((s, n))),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            try:
                p.stop()
            except RuntimeError:
                pass
        config.CONFIG_PATH = self._orig_path
        config._KEY_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _disk_text(self):
        with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def _sample(self, key="sk-SECRET-123", mid="m1"):
        return {"models": [{"id": mid, "name": "A", "base_url": "https://api.example.com",
                            "api_key": key, "model_id": "qwen-max"}],
                "current_model": mid}

    # ── 核心：不落明文 ──
    def test_api_key_not_plaintext_on_disk(self):
        config.save_config(self._sample())
        raw = self._disk_text()
        self.assertNotIn("sk-SECRET-123", raw, "API Key 仍然明文落盘")
        self.assertEqual(self.stored.get(("model", "m1")), "sk-SECRET-123",
                         "API Key 未写入系统凭据库")

    def test_api_key_hydrated_on_load(self):
        """磁盘上没有了，但内存里必须照旧拿得到（调用方零改动）"""
        config.save_config(self._sample())
        loaded = config.load_config()
        self.assertEqual(loaded["models"][0]["api_key"], "sk-SECRET-123")

    def test_get_current_model_returns_key(self):
        config.save_config(self._sample(key="sk-CUR", mid="m4"))
        m = config.get_current_model()
        self.assertEqual(m["api_key"], "sk-CUR")
        self.assertEqual(m["base_url"], "https://api.example.com")

    def test_save_models_roundtrip(self):
        """走 save_models / load_models 的正式路径同样受保护"""
        models, cid = config.load_models()
        models = [{"id": "mx", "name": "X", "base_url": "https://b",
                   "api_key": "sk-MODELS", "model_id": "mm"}]
        config.save_models(models, "mx")
        self.assertNotIn("sk-MODELS", self._disk_text())
        got, got_id = config.load_models()
        self.assertEqual(got_id, "mx")
        self.assertEqual(got[0]["api_key"], "sk-MODELS")

    # ── 旧扁平字段 ──
    def test_flat_legacy_field_also_protected(self):
        config.save_config({"api_key": "sk-FLAT-1", "base_url": "https://y"})
        self.assertNotIn("sk-FLAT-1", self._disk_text())
        self.assertEqual(config.load_config()["api_key"], "sk-FLAT-1")

    # ── 降级红线 ──
    def test_kept_plaintext_when_keyring_unavailable(self):
        """keyring 不可用时必须保留明文 —— 否则用户 Key 直接丢失"""
        with mock.patch.object(_secret_store, "is_available", return_value=False):
            config.save_config(self._sample(key="sk-KEEP"))
        self.assertIn("sk-KEEP", self._disk_text(),
                      "keyring 不可用却清空了磁盘明文 → 会永久丢 Key")

    # ── 旧数据兼容 ──
    def test_legacy_plaintext_still_loadable(self):
        """磁盘已有明文（历史数据）→ 原样读出，不得报错或清空"""
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"models": [{"id": "m3", "api_key": "sk-OLD"}]}, f)
        self.assertEqual(config.load_config()["models"][0]["api_key"], "sk-OLD")

    def test_missing_config_returns_empty(self):
        self.assertEqual(config.load_config(), {})

    # ── 启动期一次性迁移：存量明文也要被清掉 ──
    def _write_plaintext_cfg(self, key="sk-LEGACY"):
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"models": [{"id": "m9", "api_key": key}]}, f)

    def test_migrate_moves_plaintext_to_keyring(self):
        self._write_plaintext_cfg()
        moved, detail = config.migrate_api_keys_to_keyring()
        self.assertTrue(moved, f"未发生迁移: {detail}")
        self.assertNotIn("sk-LEGACY", self._disk_text(), "迁移后磁盘仍是明文")
        self.assertEqual(self.stored.get(("model", "m9")), "sk-LEGACY",
                         "迁移后凭据库里没有 Key")

    def test_migrate_is_idempotent(self):
        self._write_plaintext_cfg()
        config.migrate_api_keys_to_keyring()
        moved, _ = config.migrate_api_keys_to_keyring()
        self.assertFalse(moved, "第二次迁移应为 no-op")

    def test_migrate_skips_when_keyring_unavailable(self):
        """keyring 不可用时绝不动磁盘（否则用户 Key 直接丢失）"""
        self._write_plaintext_cfg(key="sk-DONT-LOSE")
        with mock.patch.object(_secret_store, "is_available", return_value=False):
            moved, _ = config.migrate_api_keys_to_keyring()
        self.assertFalse(moved)
        self.assertIn("sk-DONT-LOSE", self._disk_text(),
                      "keyring 不可用时迁移清空了明文 → 会丢 Key")

    def test_migrate_noop_without_plaintext(self):
        config.save_config(self._sample())          # 已是脱敏版
        moved, _ = config.migrate_api_keys_to_keyring()
        self.assertFalse(moved)

    # ── 调用方拿到的字典不被"顺手清空" ──
    def test_caller_dict_not_mutated(self):
        cfg = self._sample(key="sk-LIVE")
        config.save_config(cfg)
        self.assertEqual(cfg["models"][0]["api_key"], "sk-LIVE",
                         "save_config 不应改动调用方传入的字典（内存态要保持可用）")


if __name__ == "__main__":
    unittest.main()
