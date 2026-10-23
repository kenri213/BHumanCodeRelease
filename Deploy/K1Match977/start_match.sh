#!/usr/bin/env bash
set -euo pipefail
action="${1:-start}"
[[ "$#" -le 1 ]] || { echo 'Usage: start_match.sh [start|status|logs|stop|rollback]' >&2; exit 2; }
case "$action" in
  start)
    echo '先将192.168.50.37裁判机设INITIAL。首次启动会初始化一次；已运行时只检查新鲜感知，不重启服务、相机或模型。'
    echo '等 ready_for_operator=true 后：F1长按约2秒松开 → 摇杆回中 → 先按住RB再按A，两键长按约3秒松开，内部自动准备并起身。无需RB+B。'
    echo '起身后全松、摇杆回中即可遥控；回中停步。'
    echo '当前继承996行为：手柄正常断联会停步并锁Stand，不论之前遥控或自动；重连需真实回中，再LT＋“＋/－”按下松开重新交权。旧杆量不续跑。'
    echo 'READY/SET/PLAY中仍可LT＋“＋”接管遥控，LT＋“－”交回自动；实体F1与STOP保护优先。'
    echo 'INITIAL中摆正指定上场位置/朝向，LT＋“－”交回自动；裁判READY自动落位，SET停住，PLAY比赛。无需RB+Y。'
    echo '入场点必须与本机配置一致；不能任意摆放后指望白线自动证明半场。'
    echo '实体F1进入Emergency/Damping但不退出APP；单按X不再全局终止APP。RB+X保留官方映射F1/unstiff语义；卸力不是自动坐下，请保持净空。'
    echo '同一APP内长按F1松开可回Unstiff，无需stop/start，不自动Prepare或起身；本977不加5帧/500毫秒F1模式门槛。'
    echo '退出整个项目用本脚本 stop；无固定比赛时限。每场只切裁判状态，无需stop/start。'
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_977_from996/match_control.py start --execute --operator-present ;;
  stop|rollback)
    echo 'WARNING: stopping may switch to Damping and remove support. Keep people clear; support the robot.' >&2
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_977_from996/match_control.py "$action" --execute ;;
  status)
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_977_from996/match_control.py status --execute ;;
  logs)
    exec /usr/bin/journalctl -u bh977-from996-match.service -f -o cat ;;
  *) echo 'Usage: start_match.sh [start|status|logs|stop|rollback]' >&2; exit 2 ;;
esac
