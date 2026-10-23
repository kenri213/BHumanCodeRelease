#!/usr/bin/env bash
set -euo pipefail
action="${1:-start}"
[[ "$#" -le 1 ]] || { echo 'Usage: start_match.sh [start|status|logs|stop|rollback]' >&2; exit 2; }
case "$action" in
  start)
    echo '提起前先承托提手，再按实体STAND锁PRE；PRE没有平衡能力，必须持续承托。F1/X是卸力，不是PRE。'
    echo '直立摆回约定的1号长边线入口、身体朝场内，松开旧键，新长按实体WALK超过2秒；仍罚下时只站稳等待本队本人的处罚NONE。'
    echo '新WALK须从新鲜匹配、允许主状态且非STOP的上下文开始；接纳后的普通断包不撤长按，最终仍须鲜包/未罚/真实Stand才AUTO，无需第二次WALK。'
    echo '一次WALK完成后站稳等真实解罚；裁判NONE/active鲜包到达后，使用原生定位、回场头扫及角色策略，无需第二次WALK。'
    echo '本版移除v3专用进场路线和持续静稳检查，不额外加回场阶段；STOP/SET、罚下、F1/X和真实故障保护保持。'
    echo '收到解罚后的新鲜合法包且真实Stand稳定满300ms才AUTO；FINISHED/身份失配、新STAND/F1/X/真故障等仍撤销请求。'
    echo 'Pickup中若卧姿，走合法的新RB+A起身，仍先Stand、真实回中和LT交权，不自动AUTO。'
    echo '先将192.168.50.37裁判机设INITIAL。首次启动会初始化一次；已运行时只检查新鲜感知，不重启服务、相机或模型。'
    echo '等 ready_for_operator=true 后：F1长按约2秒松开 → 摇杆回中 → 先按住RB再按A，两键长按约3秒松开，内部自动准备并起身。无需RB+B。'
    echo '起身后全松、摇杆回中即可遥控；回中停步。'
    echo '当前继承996行为：手柄正常断联会停步并锁Stand，不论之前遥控或自动；重连需真实回中，再LT＋“＋/－”按下松开重新交权。旧杆量不续跑，X/F1仍急停。'
    echo 'READY/SET/PLAY中仍可LT＋“＋”接管遥控，LT＋“－”交回自动；X/F1与STOP保护优先。'
    echo 'INITIAL中摆正指定上场位置/朝向，LT＋“－”交回自动；裁判READY自动落位，SET停住，PLAY比赛。无需RB+Y。'
    echo '入场点必须与本机配置一致；不能任意摆放后指望白线自动证明半场。'
    echo 'X/F1停止可能卸力倒下；无固定比赛时限。每场只切裁判状态，无需stop/start。'
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_987/match_control.py start --execute --operator-present ;;
  stop|rollback)
    echo 'WARNING: stopping may switch to Damping and remove support. Keep people clear; support the robot.' >&2
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_987/match_control.py "$action" --execute ;;
  status)
    exec sudo /usr/bin/python3 -I /opt/booster/bhuman_match_987/match_control.py status --execute ;;
  logs)
    exec /usr/bin/journalctl -u bh987-match.service -f -o cat ;;
  *) echo 'Usage: start_match.sh [start|status|logs|stop|rollback]' >&2; exit 2 ;;
esac
