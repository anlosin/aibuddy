"""SQL 助手 — 编写/解释/优化 SQL，并真正连接数据库执行查询

相比旧版（仅 SYSTEM_PROMPT 提示），现在支持真实连接：
- SQLite：零依赖，直接指定文件路径即可（最适合内网轻量场景）
- MySQL / PostgreSQL：懒加载对应连接器，未安装时给出安装提示
- 默认只读模式，拦截写操作（INSERT/UPDATE/DELETE/DROP 等）
- 连接配置持久化到 db_connections.json，可跨调用/会话复用

工具：db_connect 配置并测试连接、db_list_tables 列出表、
db_schema 查看表结构、db_query 执行查询（默认只读）。
"""
import os
import json
import re

PLUGIN_INFO = {
    "name": "sql_helper",
    "description": "编写、解释、优化 SQL，并可真正连接 SQLite/MySQL/PostgreSQL 执行查询。默认只读，防止误写。",
    "version": "2.0",
}

SYSTEM_PROMPT = """你是一个资深数据库专家，精通 MySQL、PostgreSQL、SQLite。当用户提出 SQL 相关问题时：

1. **需求分析** — 先确认用户想要的查询语义和预期结果
2. **编写 SQL** — 给出格式化良好的 SQL 语句，标注方言差异
3. **逐段解释** — 解释 JOIN、子查询、窗口函数、CTE 等关键部分
4. **索引建议** — 针对查询给出表索引建议（EXPLAIN 分析思路）
5. **性能考量** — 指出潜在的性能瓶颈和优化方向
6. **安全提醒** — 如果是动态拼接 SQL，提醒 SQL 注入风险

如果涉及真实数据查询，先用 db_connect 建立连接，再用 db_query 执行；
涉及写操作请明确告知用户风险，并建议开启 read_only=False（需用户确认）。
对于查询优化类问题，先分析现有 SQL 的执行计划预期瓶颈，再给出等价改写。
对于建表/设计问题，给出规范化的表结构（包含字段类型、约束、注释）。"""

CONN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db_connections.json")

# 写操作关键字（只读模式下拦截）
WRITE_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE"
    r"|MERGE|ATTACH|DETACH)\b", re.IGNORECASE)

ROW_LIMIT = 500      # 单次查询结果行数上限
QUERY_TIMEOUT = 30   # 查询超时（秒）

# 模块级连接缓存（跨同进程内多次工具调用复用）
_CONN = {}
_CFG = {}


def _load_cfg():
    global _CFG
    try:
        if os.path.exists(CONN_FILE):
            with open(CONN_FILE, "r", encoding="utf-8") as f:
                _CFG = json.load(f)
    except Exception:
        _CFG = {}


def _save_cfg():
    try:
        with open(CONN_FILE, "w", encoding="utf-8") as f:
            json.dump(_CFG, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _connect(cfg):
    """根据配置建立连接，返回 (conn, err)"""
    db_type = (cfg.get("type") or "sqlite").lower()
    try:
        if db_type == "sqlite":
            import sqlite3
            path = cfg.get("path") or ":memory:"
            conn = sqlite3.connect(path, timeout=QUERY_TIMEOUT)
            conn.row_factory = sqlite3.Row
            return conn, None
        if db_type == "mysql":
            import mysql.connector
            conn = mysql.connector.connect(
                host=cfg.get("host", "127.0.0.1"),
                port=int(cfg.get("port", 3306)),
                user=cfg.get("user"),
                password=cfg.get("password", ""),
                database=cfg.get("database"),
                connection_timeout=QUERY_TIMEOUT,
            )
            return conn, None
        if db_type == "postgresql":
            import psycopg2
            conn = psycopg2.connect(
                host=cfg.get("host", "127.0.0.1"),
                port=int(cfg.get("port", 5432)),
                user=cfg.get("user"),
                password=cfg.get("password", ""),
                dbname=cfg.get("database"),
                connect_timeout=QUERY_TIMEOUT,
            )
            return conn, None
        return None, f"不支持的数据库类型: {db_type}"
    except ImportError as e:
        tip = {
            "mysql": "请先安装 mysql-connector-python：pip install mysql-connector-python",
            "postgresql": "请先安装 psycopg2：pip install psycopg2-binary",
        }.get(db_type, str(e))
        return None, f"缺少依赖：{tip}"
    except Exception as e:
        return None, f"连接失败: {e}"


def _get_conn(name):
    """获取（必要时重连）指定名称的连接"""
    if name in _CONN and _CONN[name] is not None:
        try:
            # 简单探活
            _CONN[name].cursor().execute("SELECT 1")
            return _CONN[name], None
        except Exception:
            _CONN[name] = None
    _load_cfg()
    cfg = _CFG.get(name)
    if not cfg:
        return None, f"未找到连接配置 '{name}'，请先调用 db_connect"
    conn, err = _connect(cfg)
    if err:
        return None, err
    _CONN[name] = conn
    return conn, None


def _do_connect(args):
    name = args.get("name") or "default"
    cfg = {
        "type": (args.get("type") or "sqlite").lower(),
        "host": args.get("host"),
        "port": args.get("port"),
        "user": args.get("user"),
        "password": args.get("password", ""),
        "database": args.get("database"),
        "path": args.get("path"),
    }
    conn, err = _connect(cfg)
    if err:
        return f"连接测试失败: {err}"
    # 探活
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
    except Exception as e:
        return f"连接建立但探活失败: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    _load_cfg()
    _CFG[name] = cfg
    _save_cfg()
    return f"✅ 连接成功并已保存: [{name}] 类型={cfg['type']}"


def _do_list_tables(args):
    name = args.get("name") or "default"
    conn, err = _get_conn(name)
    if err:
        return err
    try:
        cur = conn.cursor()
        db_type = (_CFG[name].get("type") or "sqlite").lower()
        if db_type == "sqlite":
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            rows = [r[0] for r in cur.fetchall()]
        elif db_type == "mysql":
            cur.execute("SHOW TABLES")
            rows = [list(r)[0] for r in cur.fetchall()]
        else:  # postgresql
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name")
            rows = [r[0] for r in cur.fetchall()]
        if not rows:
            return "（该数据库中没有表）"
        return f"数据库 [{name}] 共 {len(rows)} 张表:\n" + "\n".join(f"  • {t}" for t in rows)
    except Exception as e:
        return f"列出表失败: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _do_schema(args):
    name = args.get("name") or "default"
    table = args.get("table", "")
    if not table:
        return "错误: 未提供 table"
    conn, err = _get_conn(name)
    if err:
        return err
    try:
        cur = conn.cursor()
        db_type = (_CFG[name].get("type") or "sqlite").lower()
        if db_type == "sqlite":
            cur.execute(f"PRAGMA table_info({table})")
            rows = cur.fetchall()
            if not rows:
                return f"表 '{table}' 不存在或无法读取"
            lines = []
            for c in rows:
                # cid, name, type, notnull, dflt, pk
                lines.append(f"  {c[1]}  {c[2]}{'  PK' if c[5] else ''}{'  NOT NULL' if c[3] else ''}")
            return f"表 [{name}].{table} 结构:\n" + "\n".join(lines)
        if db_type == "mysql":
            cur.execute(f"DESCRIBE {table}")
            rows = cur.fetchall()
        else:
            cur.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name=%s ORDER BY ordinal_position", (table,))
            rows = cur.fetchall()
        if not rows:
            return f"表 '{table}' 不存在或无法读取"
        return f"表 [{name}].{table} 结构:\n" + "\n".join(f"  {r[0]}  {r[1]}" for r in rows)
    except Exception as e:
        return f"读取表结构失败: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _do_query(args):
    name = args.get("name") or "default"
    sql = args.get("sql", "").strip()
    if not sql:
        return "错误: 未提供 sql"
    read_only = args.get("read_only", True)
    if isinstance(read_only, str):
        read_only = read_only.lower() in ("1", "true", "yes")
    if read_only and WRITE_RE.match(sql):
        return ("⛔ 当前为只读模式，已拦截写操作。如需执行，请将 read_only 设为 false"
                "（并确保你了解风险），或改用数据库管理工具。")
    conn, err = _get_conn(name)
    if err:
        return err
    try:
        cur = conn.cursor()
        cur.execute(sql)
        if sql.lower().startswith(("select", "with", "pragma", "show", "explain", "describe")):
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            n = len(rows)
            head = rows[:ROW_LIMIT]
            table = [_row_to_str(cols, r) for r in head]
            result = f"返回 {n} 行（显示前 {len(head)} 行）:\n列: {', '.join(map(str, cols))}\n"
            result += "\n".join(table)
            if n > ROW_LIMIT:
                result += f"\n... [已截断，剩余 {n - ROW_LIMIT} 行]"
            return result
        else:
            conn.commit()
            try:
                affected = cur.rowcount
            except Exception:
                affected = "?"
            return f"✅ 执行成功，影响行数: {affected}"
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return f"查询失败: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _row_to_str(cols, row):
    if isinstance(row, dict):
        return " | ".join(f"{cols[i]}={row[cols[i]]}" for i in range(len(cols)))
    try:
        return " | ".join(str(v) for v in row)
    except Exception:
        return str(row)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "db_connect",
            "description": "配置并测试数据库连接，配置会持久化以便后续复用。SQLite 只需提供 path；MySQL/PostgreSQL 提供 host/port/user/password/database。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "type": {"type": "string", "description": "数据库类型: sqlite / mysql / postgresql，默认 sqlite"},
                    "path": {"type": "string", "description": "SQLite 文件路径（type=sqlite 时必填）"},
                    "host": {"type": "string", "description": "主机地址（mysql/postgresql）"},
                    "port": {"type": "integer", "description": "端口（mysql 默认 3306，postgresql 默认 5432）"},
                    "user": {"type": "string", "description": "用户名"},
                    "password": {"type": "string", "description": "密码"},
                    "database": {"type": "string", "description": "数据库名"}
                },
                "required": ["type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_list_tables",
            "description": "列出数据库中的所有表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_schema",
            "description": "查看指定表的结构（字段名、类型、主键、非空）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "table": {"type": "string", "description": "表名"}
                },
                "required": ["table"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": "执行 SQL 查询并返回结果。默认只读模式会拦截 INSERT/UPDATE/DELETE/DROP 等写操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "sql": {"type": "string", "description": "要执行的 SQL（SELECT/SHOW/PRAGMA 等查询语句）"},
                    "read_only": {"type": "boolean", "description": "是否只读，默认 true。false 允许写操作（谨慎）", "default": True}
                },
                "required": ["sql"]
            }
        }
    },
]


def execute(name, arguments):
    handlers = {
        "db_connect": _do_connect,
        "db_list_tables": _do_list_tables,
        "db_schema": _do_schema,
        "db_query": _do_query,
    }
    fn = handlers.get(name)
    if fn:
        return fn(arguments)
    return f"未知工具: {name}"
