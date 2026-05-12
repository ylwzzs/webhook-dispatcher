# Webhook Dispatcher

Webhook 调度中心：解密、规则匹配、上下文注入，转发回调事件到 Hermes AI。

## 架构

```
外部平台回调 → Nginx(+鉴权) → dispatcher → 解密 → 规则匹配 → Hermes AI → 回复
```

## 快速安装

```bash
curl -fsSL https://raw.githubusercontent.com/ylwzzs/webhook-dispatcher/main/install.sh | bash
```

升级也是同一条命令。

## 本地开发

```bash
git clone https://github.com/ylwzzs/webhook-dispatcher
cd webhook-dispatcher
pip install -e ".[dev]"
cp webhook_dispatcher/config.example.yaml webhook_dispatcher/config.yaml
# 编辑 config.yaml 填入真实密钥
pytest -v
uvicorn webhook_dispatcher.dispatcher:app --port 8646
```

## 功能

- **多平台解密** — 企微 AES-CBC、支付宝 RSA2，可扩展
- **规则引擎** — 按顺序匹配，支持嵌套条件、热更新
- **转发重试** — 失败自动重试，死信日志可回溯
- **管理页面** — `http://host:8646/admin` 可视化配置凭据/路由/规则
- **冲突检测** — 添加规则时自动检测互斥/遮蔽
- **内网鉴权** — shared_secret / ip_whitelist / none
- **事件日志** — JSONL 按天轮转，保留 7 天
- **inject_to** — 回调事件注入 Hermes 已有会话

## 配置

编辑 `config.yaml`：

```yaml
credentials:
  wecom-default:
    type: wecom_aes
    token: "YOUR_TOKEN"
    encoding_aes_key: "YOUR_KEY"
    receive_id: "YOUR_CORP_ID"

routes:
  wecom:
    credentials: wecom-default
    forward_to: wecom
    rules:
      - name: default-forward
        enabled: true
        conditions: {}
        action:
          type: forward
```

所有配置热更新，修改后无需重启。

## 支持的解密算法

| 类型 | 平台 | 算法 |
|------|------|------|
| `wecom_aes` | 企微应用回调 | AES-CBC + PKCS7 + SHA1 签名 |
| `alipay_rsa` | 支付宝回调 | RSA2 (SHA256withRSA) 验签 |

## API

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /admin` | 管理页面 |
| `GET /api/credentials` | 列出凭据 |
| `POST /api/credentials` | 创建凭据 |
| `GET /api/routes` | 列出路由 |
| `POST /api/routes` | 创建路由 |
| `GET /api/routes/{name}/rules` | 列出规则 |
| `POST /api/routes/{name}/rules` | 添加规则（含冲突检测） |
| `GET /api/stats` | 统计信息 |

## License

MIT
