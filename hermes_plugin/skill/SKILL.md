---
name: webhook-dispatcher
description: "Webhook 调度中心：配置回调路由、解密、规则和上下文约定"
version: 2.0.0
metadata:
  hermes:
    tags: [webhook, callback, wecom, alipay, 规则引擎, 上下文]
---

# Webhook 调度中心

## 架构

外部平台回调 → Nginx(+鉴权header) → dispatcher(:8646) → 解密 → 规则匹配 → Hermes webhook(inject_to) → AI 回复

## 关键文件

| 文件 | 作用 |
|------|------|
| `dispatcher/config.yaml` | 路由+凭据+规则+鉴权+重试配置 |
| `dispatcher/config.py` | 配置管理器，全项热更新 |
| `dispatcher/dispatcher.py` | 主服务，路由处理 |
| `dispatcher/forwarder.py` | 转发+重试+死信 |
| `dispatcher/eventlog.py` | JSONL 事件日志，按天轮转 |
| `dispatcher/auth.py` | 内网鉴权中间件 |
| `dispatcher/rules.py` | 规则引擎 |
| `dispatcher/decryptors/` | 解密器（wecom_aes / alipay_rsa） |
| `~/.hermes/webhook_subscriptions.json` | Hermes webhook 路由（含 inject_to） |
| `~/.hermes/plugins/webhook-dispatcher/patch_webhook_inject.py` | Hermes 升级后重跑 |
| `~/.hermes/plugins/webhook-dispatcher/logs/` | 事件日志目录 |

## 凭据配置（credentials）

凭据和解密算法分离。同类型应用共用算法，各配不同凭据：

```yaml
credentials:
  wecom-hr:
    type: wecom_aes
    token: "xxx"
    encoding_aes_key: "yyy"
    receive_id: "zzz"
  alipay-main:
    type: alipay_rsa
    alipay_public_key: "MIIB..."

routes:
  wecom-hr:
    credentials: wecom-hr    # 引用凭据名
    rules: [...]
```

支持的解密算法：`wecom_aes`（企微 AES-CBC）、`alipay_rsa`（支付宝 RSA2 验签）

## 全项热更新

修改 `dispatcher/config.yaml` 中的任何配置（凭据、路由、规则、鉴权等）后无需重启，下次请求自动生效。

## 转发重试

```yaml
forward:
  max_retries: 2    # 最多重试次数
  retry_delay: 1    # 退避基数（秒）
```

转发失败时自动重试，指数退避。所有重试失败后记录到死信日志。

## 事件日志

- 位置：`~/.hermes/plugins/webhook-dispatcher/logs/events-YYYY-MM-DD.jsonl`
- 死信：`~/.hermes/plugins/webhook-dispatcher/logs/dead-YYYY-MM-DD.jsonl`
- 自动保留 7 天
- `/health` 接口返回统计信息

## 内网鉴权

```yaml
server:
  auth:
    mode: shared_secret        # shared_secret | ip_whitelist | none
    shared_secret: "xxx"       # Nginx 转发时带 X-Dispatcher-Secret header
    ip_whitelist:              # ip_whitelist 模式
      - 127.0.0.1
      - ::1
```

- `shared_secret`：Nginx 加 `proxy_set_header X-Dispatcher-Secret "xxx"`
- `ip_whitelist`：只允许指定 IP
- `none`：无鉴权（默认，向后兼容）
- `/health` 始终免鉴权

## 规则配置

每个路由下有 rules 数组，按顺序匹配第一条：

```yaml
rules:
  - name: rule-name
    enabled: true
    conditions:             # 匹配条件，所有条件 AND
      Event: approval_status_change
    action:
      type: forward         # ignore | forward | ai_process
    context: "收到审批变更时通知用户"
    prompt: "处理此事件"
```

## 添加规则流程（必须遵循）

当用户要求添加新规则时，**必须**先执行冲突检测，再写入配置。

### 步骤 1：读取当前规则

读取 `dispatcher/config.yaml` 中目标路由的 rules 列表。

### 步骤 2：冲突检测

逐一分析新规则与每条现有规则的关系：

**互斥**：条件完全相同，动作不同。后写的规则永远不命中。

**遮蔽（新规则被吞）**：新规则更具体但排在已有宽泛规则之后，会被吞掉，必须插到前面。

**被遮蔽（现有规则失效）**：新规则更宽泛且动作不同，插在前面会导致已有具体规则失效。

**部分重叠**：条件在某些 key 上重叠，某些 payload 可能只命中一条。

### 步骤 3：提示用户选择

检测到冲突或遮蔽时，**禁止直接写入**，向用户说明并给出选项（调整位置/修改条件/取消）。

### 步骤 4：写入规则

确认后写入 config.yaml，无需重启，热更新生效。

## 添加新平台路由

1. config.yaml 的 credentials 添加凭据
2. routes 下添加路由名，credentials 引用凭据
3. 配置 rules 和 forward_to
4. webhook_subscriptions.json 添加对应路由（含 inject_to）
5. Nginx 添加 location 代理到 :8646，加 X-Dispatcher-Secret header
6. systemctl --user restart webhook-dispatcher

## inject_to 机制

webhook_subscriptions.json 中的 inject_to 让回调事件注入到指定 platform 的 session：

```json
{
  "wecom": {
    "secret": "...",
    "deliver": "wecom",
    "deliver_extra": {"chat_id": "ZhangDuo"},
    "inject_to": {"platform": "wecom", "chat_id": "ZhangDuo", "user_id": "ZhangDuo"},
    "prompt": "{_rule_prompt}\n\n上下文约定：{_rule_context}\n\n事件数据：{__raw__}"
  }
}
```

## Hermes 升级后恢复

```bash
python3 ~/.hermes/plugins/webhook-dispatcher/patch_webhook_inject.py
systemctl --user restart hermes-gateway.service
```

## 诊断

运行 `/webhook-doctor` 检查所有组件状态（含鉴权、重试、事件日志等 13 项检查）。
