# K1实体Pickup分支执行约定

2026-10-24用户新增验收：987当前v4已通过用户现场Pickup后回场测试，用户授权提交该版本。验收来源是operator_reported，不修改历史安装回执中NOT RUN，不扩大为全部比赛/网络/摆位场景验收。提交只含本分支源码、测试、当前987启动层文本与说明，不混996/977其它改动，不提交ELF/缓存/厂商原件/系统备份/凭据。详见Docs/K1_987_NATIVE_REENTRY_V4_20261024.md及Deploy/K1Match987/RELEASE.json。

文件安装阶段记录（历史）：本分支为codex/987-pickup-native-reentry-v4，基座48fd18a。仅保留普通GC间隙修复与日志目录，原生定位/回场/Physical/Joystick/策略逐字v2，无v3几何状态机。完整ARM及405+41源码相机回归通过，APP2ea1b44/controllerbbfbc800已在987仅文件安装，260保护不变、缓存只重绑APP。当时未启动/运动/实测新轨迹，未提交推送；旧v3源码和失败证据保留。实际回执位于本轮root INSTALL_RECEIPT_987_V4.json和INSTALL_POST_VERIFIED_V4.json；后续用户验收以上段为准。

先完整阅读`Docs/K1_PICKUP_SINGLE_WALK_REENTRY_20261024.md`；旧`Docs/K1_PHYSICAL_PICKUP_STAND_20261024.md`为v1历史。
基座35b4d3为996实装37a2源码，本分支只补实体STAND退出/PRE锁定与新WALK合法回场，以及987已有身份入口。
不得顺便迁入其它机器人的BodyGap、头隔离、反馈线程、路线或策略候选。

- 区分文件部署、启动和身体测试；用户授权代码/同步不等于允许远程起身或行走。
- 按SoC核身份，保留每机Config/模型/PD/标定/SDK及旧版；996与987不能互相照搬整个启动器或缓存。
- 私有备份mode与运行原件mode分别取证。实际SHA/uid/gid/mode/bytes与路由补丁对必须在写入前核验。
  不用改正常原件权限、删检查或盲重刷来掩盖清单错误。
- 源码封版再编译；最后修复后重新构建并确认Ninja无剩余工作。APP/controller/core/清单/验证器须同版。
- PRE不平衡，需要承托。用户本轮明确改为一次WALK确认放回、等待真实解罚再自动回场；允许罚下时建待票，不允许无合法新鲜上下文的任意旧持键续票。已完成待票在STOP/SET/旧包时暂停，真实新鲜合法解罚后才AUTO；F1/X及真实故障仍撤票优先。本轮只部署测试987，不改996实装。
- 模块持久状态不得放进会被CABSL初始fallback重建的vars；真实Fall/GetUp按原生isDone和恢复规则离相位。
- 仅提交明确的源码、测试、文档和同版启动器。禁止整目录add；不新增私钥/密码、ELF、固件、系统备份或TensorRT缓存。
- 原失败、修正、回归及安装证据分别保存；软件/文件通过不能冒充现场运动或比赛验收。
