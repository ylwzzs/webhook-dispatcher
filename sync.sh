#!/usr/bin/env bash
# Webhook Dispatcher — 服务器端代码同步工具
# 流程：服务器改动推到 prod-patches 分支 → 本地审核 → 合并到 main → 服务器拉取 main
#
# 用法:
#   ./sync.sh push "修复了XX"   — 提交服务器改动，推到 prod-patches（待审核）
#   ./sync.sh pull              — 从 GitHub 拉取 main 并重启（只拉审核过的代码）
#   ./sync.sh rollback          — 回滚到上一个提交
#   ./sync.sh status            — 查看当前状态
#   ./sync.sh diff              — 查看未提交的改动

set -e

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/webhook-dispatcher"
cd "$PLUGIN_DIR"

PATCH_BRANCH="prod-patches"

case "${1:-status}" in

  push)
    MSG="${2:-auto: sync from production $(date +%Y-%m-%d/%H:%M)}"
    # 确保在 prod-patches 分支上
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ "$BRANCH" != "$PATCH_BRANCH" ]; then
      if git show-ref --verify --quiet "refs/heads/$PATCH_BRANCH"; then
        git checkout "$PATCH_BRANCH"
      else
        git checkout -b "$PATCH_BRANCH"
      fi
    fi
    # 只提交代码文件，排除 config.yaml 和运行时文件
    git add webhook_dispatcher/ hermes_plugin/ tests/ pyproject.toml install.sh sync.sh systemd/ README.md .gitignore .github/
    if git diff --cached --quiet; then
      echo "没有需要提交的改动"
    else
      git commit -m "$MSG"
      git push -u origin "$PATCH_BRANCH"
      echo "✓ 已推送到 $PATCH_BRANCH 分支（待本地审核）"
      echo "  本地审核: git fetch origin && git diff main..origin/$PATCH_BRANCH"
      echo "  审核通过: git merge origin/$PATCH_BRANCH && git push origin main"
    fi
    ;;

  pull)
    # 只从 main 拉取审核过的代码
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ "$BRANCH" != "main" ]; then
      git checkout main
    fi
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
    echo "=== 当前分支 ==="
    git branch -v
    echo ""
    echo "=== main vs $PATCH_BRANCH ==="
    git log --oneline main..$PATCH_BRANCH 2>/dev/null | head -10 || echo "(无法比较)"
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
    echo "  push [msg]    提交服务器改动，推到 prod-patches（待审核）"
    echo "  pull          从 main 拉取审核过的代码并重启"
    echo "  rollback [n]  回滚到指定提交（默认上一个）"
    echo "  status        查看当前状态"
    echo "  diff          查看未提交的改动"
    ;;
esac
