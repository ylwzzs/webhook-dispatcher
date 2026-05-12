#!/usr/bin/env bash
# Webhook Dispatcher — 一键安装/升级脚本
# 安装: curl -fsSL https://raw.githubusercontent.com/ylwzzs/webhook-dispatcher/main/install.sh | bash
# 升级: 同一条命令

set -e

REPO="https://github.com/ylwzzs/webhook-dispatcher"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/webhook-dispatcher"

echo "🔧 Webhook Dispatcher 安装/升级"
echo "   HERMES_HOME=$HERMES_HOME"

# --- Step 1: Clone or update ---
if [ -d "$PLUGIN_DIR/.git" ]; then
    echo "⬆ 升级中..."
    cd "$PLUGIN_DIR" && git pull
else
    echo "⬇ 安装中..."
    if [ -d "$PLUGIN_DIR" ]; then
        echo "⚠ 目录 $PLUGIN_DIR 已存在但不是 git 仓库，备份后重新克隆"
        mv "$PLUGIN_DIR" "${PLUGIN_DIR}.bak.$(date +%s)"
    fi
    mkdir -p "$HERMES_HOME/plugins"
    git clone "$REPO" "$PLUGIN_DIR"
fi

# --- Step 2: Install dependencies ---
cd "$PLUGIN_DIR"

# Prefer pip install -e if available, otherwise venv
if command -v pip3 &>/dev/null; then
    echo "📦 安装 Python 依赖..."
    pip3 install -e . 2>/dev/null || pip3 install fastapi uvicorn httpx cryptography pyyaml
else
    # Fallback: create venv
    VENV_DIR="$PLUGIN_DIR/.venv"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install -q fastapi uvicorn httpx cryptography pyyaml
fi

# --- Step 3: Config ---
CONFIG_FILE="$PLUGIN_DIR/webhook_dispatcher/config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    cp "$PLUGIN_DIR/webhook_dispatcher/config.example.yaml" "$CONFIG_FILE"
    echo ""
    echo "⚠️  首次安装！请编辑配置文件填入真实密钥："
    echo "   $CONFIG_FILE"
    echo ""
fi

# --- Step 4: Systemd service ---
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/webhook-dispatcher.service"
mkdir -p "$SERVICE_DIR"

# Detect python path
if [ -d "$PLUGIN_DIR/.venv" ]; then
    PYTHON_BIN="$PLUGIN_DIR/.venv/bin/python"
    UVICORN_BIN="$PLUGIN_DIR/.venv/bin/uvicorn"
else
    PYTHON_BIN="$(which python3)"
    UVICORN_BIN="$(which uvicorn 2>/dev/null || echo '$(pip3 show uvicorn | grep Location | cut -d' ' -f2)/../bin/uvicorn')"
fi

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Webhook Dispatcher - Decrypt, Rule-match, Forward to Hermes
After=network.target

[Service]
Type=simple
WorkingDirectory=$PLUGIN_DIR
ExecStart=$UVICORN_BIN webhook_dispatcher.dispatcher:app --host 0.0.0.0 --port 8646
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload 2>/dev/null || true

# --- Step 5: Start/restart ---
if systemctl --user is-active webhook-dispatcher &>/dev/null; then
    systemctl --user restart webhook-dispatcher
    echo "✓ 服务已重启"
else
    systemctl --user start webhook-dispatcher 2>/dev/null || true
    systemctl --user enable webhook-dispatcher 2>/dev/null || true
    echo "✓ 服务已启动"
fi

sleep 2

# --- Step 6: Verify ---
if curl -sf http://127.0.0.1:8646/health &>/dev/null; then
    echo "✓ 健康检查通过"
else
    echo "⚠ 健康检查失败，请检查日志: journalctl --user -u webhook-dispatcher"
fi

echo ""
echo "✅ 完成！"
echo "   管理页面: http://127.0.0.1:8646/admin"
echo "   配置文件: $CONFIG_FILE"
echo "   查看日志: journalctl --user -u webhook-dispatcher -f"
