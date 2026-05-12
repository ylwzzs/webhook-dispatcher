#!/usr/bin/env bash
# Webhook Dispatcher — 服务器端代码同步工具
# 用法:
#   ./sync.sh push "修复了XX"   — 提交服务器上的改动并推到 GitHub
#   ./sync.sh pull              — 从 GitHub 拉取最新代码并重启
#   ./sync.sh rollback          — 回滚到上一个提交
#   ./sync.sh status            — 查看当前状态
#   ./sync.sh diff              — 查看未提交的改动

set -e

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/webhook-dispatcher"
cd "$PLUGIN_DIR"

case "${1:-status}" in

  push)
    MSG="${2:-auto: sync from production $(date +%Y-%m-%d/%H:%M)}"
    # 只提交代码文件，排除 config.yaml 和运行时文件
    git add webhook_dispatcher/ hermes_plugin/ tests/ pyproject.toml install.sh systemd/ README.md .gitignore .github/
    if git diff --cached --quiet; then
      echo "没有需要提交的改动"
    else
      git commit -m "$MSG"
      git push origin main
      echo "✓ 已提交并推送到 GitHub: $MSG"
    fi
    ;;

  pull)
    git fetch origin main
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse origin/main)
    if [ "$LOCAL" = "$REMOTE" ]; then
      echo "已是最新版本"
    else
      git pull origin main
      # 重新安装依赖（如果 pyproject.toml 有变化）
      if git diff --name-only $LOCAL..REMOTE | grep -q pyproject.toml; then
        echo "依赖有变化，重新安装..."
        .venv/bin/pip install -q -e . 2>/dev/null || pip3 install -e .
      fi
      systemctl --user restart webhook-dispatcher
      echo "✓ 已拉取最新代码并重启服务"
    fi
    ;;

  rollback)
    git log --oneline -5
    echo ""
    COMMIT="${2:-HEAD~1}"
    echo "回滚到: $COMMIT"
    git revert --no-commit "$COMMIT" 2>/dev/null || git reset --hard "$COMMIT"
    git commit -m "rollback: revert $COMMIT"
    systemctl --user restart webhook-dispatcher
    echo "✓ 已回滚并重启服务"
    ;;

  status)
    echo "=== Git 状态 ==="
    git status -s
    echo ""
    echo "=== 当前版本 ==="
    git log --oneline -3
    echo ""
    echo "=== 服务状态 ==="
    systemctl --user is-active webhook-dispatcher 2>/dev/null || echo "stopped"
    ;;

  diff)
    git diff --stat
    echo ""
    git diff
    ;;

  *)
    echo "用法: $0 {push|pull|rollback|status|diff}"
    echo ""
    echo "  push [msg]    提交服务器改动并推到 GitHub"
    echo "  pull          从 GitHub 拉取最新代码并重启"
    echo "  rollback [n]  回滚到指定提交（默认上一个）"
    echo "  status        查看当前状态"
    echo "  diff          查看未提交的改动"
    ;;
esac
