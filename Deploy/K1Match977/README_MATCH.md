# 977：官方F1模式语义的同版参考启动层

这是977专属、已冻结生产脚本的源码公开副本，不是通用安装包，也不是B-Human官方逐字原样部署。
这里只含源码、模板与离线测试，没有APP/helper ELF、厂商路由原件/补丁、SDK、专属相机标定或TensorRT缓存。
**未组装发行包不能直接运行或安装；不要从源码库直接执行start_match.sh。**

## 身份与版本边界

- 硬件SoC：`1424225066425`，仅977；序列号是身份校验值，不是登录凭据。
- 已安装项目：`/home/booster/Workspace/BHuman_977_From996_20261023`。
- 控制根：`/opt/booster/bhuman_match_977_from996`；主unit：`bh977-from996-match.service`。
- 运行配置：114队 / 1号守门员 / SField约9×6米 / K1RealSense / 仅192.168.50.37。
- 算法基座：996实际37a2c4ba，对应公开提交`35b4d3cee3e8f3d8e8774a01f73cefde44ee0fd4`。
- 977参考APP：`c629b0c46d88077c0e5969da2a923de1f8e4127297209035e5c35980ac01f29f`。

完整公开构建摘要见[BUILD_INFO.json](BUILD_INFO.json)，分支说明见
[977发布说明](../../Docs/K1_977_OFFICIAL_F1_RESIDENT_20261024.md)。
重新构建因工具链、依赖与调试路径不同，ELF不保证逐字复现；必须重新核验和冻结全部实际产物pin，不能删除检查迎合旧SHA。

## 当前F1与X到底做什么

- 实体F1进入Emergency/Damping时APP仍驻留，不再顺带终止整个项目；卸力可能失去支撑，不是坐下。
- 同一APP内长按F1再松开回到Unstiff，不必stop/start；不会自动Prepare或起身。
- 单按X不再被额外解释为“全局退出APP”。**RB+X保留官方映射F1/unstiff语义**，不发明新的X模式。
- 真正退出整个项目用原安装入口的`stop`，由本入口恢复自己拥有的资源。
- 本977模式不额外要求“5帧Cognition/500毫秒”才能执行F1模式切换；首次真实连接握手及传感器/模式保护保留。

用户已要求的D455适配、实体RB修饰位、RB+A合并Prepare/起身、白名单与身份绑定继续保留。
这些本地适配不是官方原样。**没有AUTO断联连续性例外**：正常手柄REMOVE停步锁Stand，不论之前遥控还是自动；
重连需新回中和新的LT选择，不继续旧杆量。
不迁移977头隔离、BodyGap分级、647低状态接收线程、新故障票或其它比赛策略。

## 只有完成每机发行与预检后才使用的原入口

以下命令是已安装977的操作说明，**不是让源码检出目录具备部署或身体测试权限**：

```bash
~/Workspace/BHuman_977_From996_20261023/scripts/start_match.sh
~/Workspace/BHuman_977_From996_20261023/scripts/start_match.sh status
~/Workspace/BHuman_977_From996_20261023/scripts/start_match.sh logs
~/Workspace/BHuman_977_From996_20261023/scripts/start_match.sh stop
```

裁判INITIAL中启动并等`ready_for_operator=true` → 真实F1长按约2秒松开 → 全松回中 →
先RB再补A保持约3秒松开，内部Prepare/起身 → 站稳后遥控摆正本机入场位置与朝向 →
回中、LT−交自动 → READY落位、SET停住、PLAY比赛。无需额外RB+B/RB+Y。
STOP、SET、罚下、真实姿态、原始新鲜度与输入保护仍有效。身体测试需另行现场授权。
`running=true`不等于已起身、定位正确或适合比赛。

无固定10/25分钟比赛截止，Restart=no；本轮没有开机预加载或启动提速功能。
已运行时重复start只检查新鲜Cognition，不重启服务、相机、模型或APP；检查预算不等于比赛截止。
显式stop或卸力仍可能让站立机器人失去支撑；关终端或日志窗口不等于停止后台APP。

## 组装与安装的门禁

`match_control.py`、`route_transaction.py`、`start_match.sh`、`install_update.py`保持冻结生产字节。
它们包含977参考产物pin及独立Linux目录，不是可随意换串号的通用工具。
`manifest.template.json`故意不含批准元数据；私有备份0600不是live原件0755的批准来源。
须在同一目标机独立核验原路由SHA/uid/gid/mode/bytes和补丁对，取得本机SDK/音频/相机与缓存证据，
构建自己的ELF/helper并闭合bundle→core→controller→发行清单，然后才可考虑经审核的安装。
缺失或漂移时应拒绝，不能复制其它机器的证明、改正常文件权限、删身份门禁或盲刷固件。

本`install_update.py`仅支持977从旧APP`2cfaf1b0…`、controller`7179e781…`的精确同项目更新，
不是首装器，也不能重复应用于已装c629的新版本。它拒绝正在运行/有pending start或Job的状态，
保留原路由批准权限，备份原APP/controller/proof/三份旧元数据，逐项保护Config/lib/模型/PD/cache，
只在同机资源相同后重绑APP证明；不调用SDK、不启停服务、不启动APP、不自动起身。

公开源码库`Config/settings.cfg`的89队/3号/Default不是这台机器的运行配置；
`settings.cfg.example`只是脱敏示例，不会被APP自动加载。不能用它覆盖未经核验的现场专属配置。
公共Provider须保留完整jointMapping等参数，仅使用已核RB位覆盖；不能用两行局部cfg代替完整文件。
D455名义外参不是逐台实测标定，不复制别机标定/cache。

六资源租约与90秒有界等待保持：booster_camera、ros.camera、ros.booster.video_stream、
ros.booster.vision_detection、ros.booster.auto_handeye_calib、lui；不租赁motion/device_gateway/remote_controller/robot_core。
跨boot陈旧body_start_intent不授权卸当前原厂身体；同boot自己APP退出的必要恢复仍保留。

## 离线验证与已知边界

```bash
cd Deploy/K1Match977
python3 -m unittest discover -v
```

冻结发行的启动层/更新器146项回归通过，源代码277项与相机41项通过，另有31项专项回放。
这些数字不可相加成独立物理测试次数；专项可能属于总测试集合。
真机已核F1模式0→3→0保持同一APP PID，没有做本版起身、走路、踢球或完整比赛验收。
现场验证结束后程序已停止；此公开提交不触发任何机器人启动、安装或模式切换。

旧`Deploy/K1Match996`、通用commissioning和紧急回退工具仍保留各自旧scope；
它们的X/F1退出、固定试验时限或996身份说明不适用于本977入口，不据此重写或复用它们。
