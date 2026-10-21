#!/usr/bin/env bash
set -euo pipefail
action="${1:-start}"
[[ "$#" -le 1 ]] || { echo 'Usage: start_match.sh [start|status|logs|stop|rollback]' >&2; exit 2; }
case "$action" in
  start)
    echo '只接192.168.50.37裁判机。首发摆位推荐INITIAL；比赛卧姿恢复也支持READY/PLAY，须未罚下、非STOP/SET。首次启动显示10阶段；已运行时不重启服务。'
    echo '等 ready_for_operator=true 后：F1长按约2秒松开 → 摇杆回中 → 先按住RB再按A，两键长按约3秒松开，内部自动准备并起身。无需RB+B。'
    echo '起身后全松、摇杆回中即可遥控；回中停步。'
    echo '比赛中恢复起身后先站住：看到MATCH_RECOVERY_STANDING，再回中并LT＋“－”按下松开交自动。手柄断联停步站立，X/F1仍急停。'
    echo 'READY/SET/PLAY中仍可LT＋“＋”接管遥控，LT＋“－”交回自动；X/F1与STOP保护优先。'
    echo 'INITIAL中摆正指定上场位置/朝向，LT＋“－”交回自动；裁判READY自动落位，SET停住，PLAY比赛。无需RB+Y。'
    echo '入场点必须与本机配置一致；不能任意摆放后指望白线自动证明半场。'
    echo 'X/F1停止可能卸力倒下；无固定比赛时限。每场只切裁判状态，无需stop/start。'
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_996/match_control.py start --execute --operator-present ;;
  stop|rollback)
    echo 'WARNING: stopping may switch to Damping and remove support. Keep people clear; support the robot.' >&2
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_996/match_control.py "$action" --execute ;;
  status)
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_996/match_control.py status --execute ;;
  logs)
    exec /usr/bin/journalctl -u bh996-match.service -f -o cat ;;
  *) echo 'Usage: start_match.sh [start|status|logs|stop|rollback]' >&2; exit 2 ;;
esac
