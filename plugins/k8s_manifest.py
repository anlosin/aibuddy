"""K8s 部署清单生成 — 把 kubernetes-devops 技能的生产级知识桥接成 PyQt5 原生插件。

来源：WorkBuddy 技能 kubernetes-devops（SKILL.md 最佳实践 + assets/*.yaml 模板）
桥接方式：
- SYSTEM_PROMPT：注入资深 K8s 工程师的最佳实践约束（安全上下文/探针/资源/标签/反模式）
- TOOLS + execute：get_k8s_template 让模型拉取合规的起步模板，再按需求定制
- 模板内置在 k8s_templates/，不依赖技能目录，技能卸载也不影响本插件

支持的 kind：deployment / service / statefulset / cronjob / ingress / configmap / pvc / secret
"""
import os

PLUGIN_INFO = {
    "name": "k8s_manifest",
    "description": "生成生产级 Kubernetes 部署清单（Deployment/Service/StatefulSet/CronJob/Ingress/ConfigMap/PVC/Secret），内置安全上下文、健康检查与资源约束等最佳实践。",
    "version": "1.0.0",
}

SYSTEM_PROMPT = """你是一位资深 Kubernetes 工程师。当用户要求生成/修改 K8s YAML 部署配置时，必须产出生产级清单：

1. **工作负载选型**：无状态用 Deployment；有状态（数据库/队列/缓存）用 StatefulSet+PVC；定时任务用 CronJob；每节点代理用 DaemonSet。
2. **安全上下文（必须）**：
   - Pod 级：runAsNonRoot: true、runAsUser/fsGroup: 1000、seccompProfile: RuntimeDefault
   - 容器级：allowPrivilegeEscalation: false、readOnlyRootFilesystem: true、capabilities.drop: [ALL]
3. **健康检查（必须）**：每个容器配 livenessProbe + readinessProbe（慢启动加 startupProbe）。
4. **资源约束（必须）**：requests 与 limits 都设（内存 limit 约为 request 的 1.5–2 倍）。
5. **镜像标签（必须）**：永远用具体版本号，禁止 :latest。
6. **标准标签（必须）**：metadata.labels 用 app.kubernetes.io/name、/version、/component 等标准标签，selector.matchLabels 必须与之匹配。
7. **高可用**：生产环境 replicas 至少 3，配合 RollingUpdate（maxUnavailable: 0 实现零停机）。
8. **配置外置**：配置用 ConfigMap、敏感信息用 Secret（明文 Secret 禁止提交 Git，改用 Sealed Secrets/External Secrets/Vault）。

**生成流程建议**：先调用 get_k8s_template 拉取对应 kind 的合规起步模板，再按用户需求（命名空间、副本数、端口、资源规格、探针路径等）定制，避免从零写错字段。

**绝对禁止的反模式**：使用 :latest 镜像、跳过资源限制、以 root 运行、提交明文 Secret、跳过探针、省略标准标签、生产环境单副本、把配置硬编码进镜像。"""

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "k8s_templates")

# kind 别名 -> 实际文件名
_KIND_ALIASES = {
    "deployment": "deployment",
    "deploy": "deployment",
    "service": "service",
    "svc": "service",
    "statefulset": "statefulset",
    "sts": "statefulset",
    "cronjob": "cronjob",
    "cj": "cronjob",
    "ingress": "ingress",
    "ing": "ingress",
    "configmap": "configmap",
    "cm": "configmap",
    "pvc": "pvc",
    "secret": "secret",
    "secrets": "secret",
}


def _available_kinds():
    if not os.path.isdir(TEMPLATES_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(TEMPLATES_DIR) if f.endswith(".yaml"))


def _do_get_template(args):
    kind = (args.get("kind") or "").strip().lower()
    if not kind:
        return "错误: 未提供 kind。可选: " + ", ".join(_available_kinds())
    key = _KIND_ALIASES.get(kind, kind)
    path = os.path.join(TEMPLATES_DIR, f"{key}.yaml")
    if not os.path.exists(path):
        return (f"暂不支持 kind='{kind}'。可选模板: " + ", ".join(_available_kinds())
                + "\n（Secret/PVC 可用 pvc / secret；Service 别名 svc；ConfigMap 别名 cm）")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return f"读取模板失败: {e}"
    return (f"以下是 {key} 的合规起步模板，请基于此按需求定制（替换 name/namespace/镜像/端口/资源规格等）：\n\n"
            f"```yaml\n{content}```")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_k8s_template",
            "description": "获取指定 kind 的 Kubernetes 合规起步 YAML 模板（含安全上下文、探针、资源约束）。生成任何 K8s 清单前应先调用本工具拿到正确骨架，再定制。支持 deployment/service/statefulset/cronjob/ingress/configmap/pvc/secret。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "资源类型：deployment / service / statefulset / cronjob / ingress / configmap / pvc / secret（支持别名 svc、cm、sts、cj、deploy 等）"
                    }
                },
                "required": ["kind"]
            }
        }
    }
]


def execute(name, arguments):
    handlers = {
        "get_k8s_template": _do_get_template,
    }
    fn = handlers.get(name)
    if fn:
        return fn(arguments or {})
    return f"未知工具: {name}"
