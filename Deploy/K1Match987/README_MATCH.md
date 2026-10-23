# 987：原生回场＋官方F1常驻候选（未部署）

本轮是代码候选，不是987已经安装的版本；完整产物pin及软件验收见`RELEASE.json`。本轮没有连接、部署、启动或操作机器人。

本地冻结APP SHA-256：`a06145fa7c4ba989afcea08ff8c9a19797c44293e99401b185e92e5c6bf4ca8d`。完整ARM构建、435项源码／41项相机回归与103项启动层契约回归通过，均为离线软件证据。

目标保持：114队／1号守门员／9×6米SField／裁判机白名单仅192.168.50.37。
项目：`/home/booster/Workspace/BHuman_987_Match_20261023`；控制根：`/opt/booster/bhuman_match_987`；SoC1424725033885。
这些是987绑定值，不可复制977的目录、身份、批准元数据、SDK或私人标定覆盖。

## 本轮F1与X的变化

- 实体F1进入Emergency/Damping，APP继续驻留；卸力可能倒下，不是坐下。
- 同一APP内长按F1约2秒再松开可回Unstiff；不必stop/start，不自动Prepare或起身。
- 不再额外要求5帧Cognition／500毫秒才能执行F1模式切换；首次真实连接握手、传感器和模式保护不取消。
- 单按X不再全局终止APP；RB+X保留原生KeyStates的F1/unstiff映射，不定义新的X模式。
- 真正终止整个项目用入口的`stop`或SIGINT/SIGTERM，由本入口恢复自己的资源。

常驻进程跨F1授权／模式生命周期时清除旧Pickup锁和待AUTO票，防止卸力前的旧键、旧票在新授权后继续生效；实际后续操作仍需真实状态和新的实体按键。该清理不替换原生Pickup／定位算法，也不增加回场状态机。

这是对987范围的明确适配，不改变其它机器人；D455、白名单、RB和合并RB+A仍是本地适配，不能称整套部署与官方逐字一致。

## 完成发行并获现场授权后使用的操作

首次上场：INITIAL中启动并等ready_for_operator=true→真实F1长按约2秒松开→回中→先RB后A保持约3秒松开，内部Prepare/起身→站稳摆正本机入场点与朝向→LT＋“－”交自动→READY落位、SET停步、PLAY比赛。无需额外RB+B或RB+Y。

Pickup保持基座原生链：先承托提手再实体STAND锁PRE，PRE不平衡、须持续承托；直立放回合法长边线回场点、胸口朝场内，松开旧键，新长WALK超过2秒。可仍被罚下，但长按须从合法新鲜上下文开始；普通包间隙不撤同一次合法意图，短按或一开始就是旧包不能补票。

完整按键后先站稳，收到本队本人处罚NONE/active的新鲜合法裁判包、真实Stand稳定300ms才交AUTO，无需第二次WALK；之后沿用原生SelfLocator、处罚返回头扫与角色策略，不增加人工定位seed、专用进场路线或额外回场状态机。正确摆位仍必要，对称白线不能单独证明半场方向。

STOP/SET、罚下、真实故障及F1模式保护保持。卧姿仍走合法新RB+A，起身先Stand，再真实回中与LT交权，不直接AUTO。正常手柄断联仍停步并锁Stand，不增加AUTO断联连续性；重连需真实回中及新的LT选择，不续旧杆量。

原安装后的命令位置不改，但本候选在完成实际每机发行前不能直接从仓库运行：

```bash
~/Workspace/BHuman_987_Match_20261023/scripts/start_match.sh
~/Workspace/BHuman_987_Match_20261023/scripts/start_match.sh status
~/Workspace/BHuman_987_Match_20261023/scripts/start_match.sh logs
~/Workspace/BHuman_987_Match_20261023/scripts/start_match.sh stop
```

无固定10／25分钟比赛时限、Restart=no、不开机自动启动身体；六资源租约和90秒有界就绪等待不变。已运行时重复start只查新鲜Cognition，不重启APP、相机、服务或模型。F1卸力不再等于退出项目；关日志窗口也不等于停止。

## 发行和验收边界

当前来源：987基座48ce9027＋977官方F1模式来源1cf8bf14a6cc079999b807a946d6df31c528ed16。仅迁移F1所需语义，不带977跨boot恢复策略、BodyGap、头隔离、反馈线程或其它比赛策略。

本目录不含APP/helper二进制、SDK、缓存、私有运行证明或真实路由manifest。新APP完成后必须重绑controller、源码构建证据和发行清单；旧基座APP2ea1不得冒充新F1产物。
安装前须对本机SDK、批准运行元数据、相机、模型/cache及APP哈希链独立核验；不能把私有备份0600当作运行原件0755，不删除权限门禁。

基座现场Pickup回场通过属于operator_reported历史。候选的本地编译和离线回归不等于已部署，不等于已经通过真机F1、起身、定位、回场或比赛验收。
