# K1 只订阅验收工具（独立于 B-Human）

`k1_dds_observer` 只创建应用数据读取端，观察原厂与官方重定向后的按钮/手柄，以及关节反馈与设备状态。它不运行 B-Human、不修改厂商库、网络或服务，不发送动作或切换模式。DDS 自身的发现/订阅握手流量不等于机器人控制输出。

## 先做本地无 SDK 测试

```bash
cmake -S Tools/K1Commissioning -B /tmp/k1-commissioning-options
cmake --build /tmp/k1-commissioning-options
ctest --test-dir /tmp/k1-commissioning-options --output-on-failure
python3 -B Tests/K1Commissioning/test_observer_source.py
```

默认不会编译或启动设备诊断。ARM64 Linux 上可用项目 SDK（或经核验的 SDK 头/库目录）构建：

```bash
cmake -S Tools/K1Commissioning -B /tmp/k1-commissioning-build \
  -DK1_BUILD_DDS_OBSERVER=ON
cmake --build /tmp/k1-commissioning-build
```

可显式指定 `-DK1_SDK_ROOT=<SDK根目录>` 与 `-DK1_SDK_LIBRARY_DIR=<ARM64库目录>`。工具是 C++20，选项测试是 C++17；旧 SDK 没有匹配 writer 数接口时输出 `null`，不是编译失败。运行前核验 loader、SDK ABI 和 DDS profile，库使用项目私有搜索路径，不覆盖原厂库。构建及测试本身不会运行诊断。

## 运行参数（必须另有真机诊断授权）

```text
k1_dds_observer --duration-seconds 10 --interface-ip 127.0.0.1 --domain-id 0
```

- 默认仅绑定本机 `127.0.0.1`，不自动扫描网络。需要其它网卡时只接受明确数字 IPv4。
- `--duration-seconds` 是含初始化、采样和关闭在内的整数预算，范围 0～60。0 只输出配置，不初始化 DDS。现场按键联调可选 `--stop-after-f1`：新话题中收到F1按下与松开且已观察至少3秒后提前正常结束；未收到时仍受最大预算约束，不发送任何控制。
- 主线程约每秒输出一行 JSON；回调仅更新互斥快照。保留约 250ms 关闭预算，实际采样略短。
- 正常退出先停接收并关闭六个 reader，刷新最终 JSON 后 `_Exit`，避免 SDK 静态析构与回调生命周期冲突。初始化、输出或关闭卡住时硬截止以 124 退出；此时不能声称已正常关闭或采样通过。2 表示参数/SDK异常；0 表示程序完成，不等于遥控与急停已验收。
- 快照包含各话题接收计数、最新原始轴/按钮/事件、接收年龄、可选匹配 writer 数、IMU 和关节数量/有限关节样本。年龄是本机单调时钟的到达年龄，不是设备采集时间。计数是该诊断接收到的消息数，不是原厂发送总数。
- `device_gateway` 单独报告总关节数、连接/断连数量、断连关节名字（最多64项）；LowState 报告 serial/parallel 数量及头部索引0/1的原始弧度。消息中有22个关节槽位或头角数值不等于硬件在线；断连时数值可能无效或陈旧，不可据此放行动作。
- `device_gateway.aggregate` 跨**所有收到的 gateway 回调**保留最小连接数、最大断连数、出现断连的回调次数、总关节数范围、曾断连名字及各名字出现的回调次数；最新恢复22/22不会清除历史瞬断。同一名字在同一回调重复只记一次。单次名字最多64项、全程最多256个不同名字，超限明确报告截断/未追踪次数和 `name_history_complete=false`。
- 每次手柄和 LowState 回调都累计有限数检查：4个原始轴，9个IMU值，全体serial q/dq、全体parallel q/dq/tau_est及头部0/1 q/dq。输出检查回调/值数、异常回调/非有限值总数；NaN/Inf不会因为后续恢复正常而消失。此统计不表示检查了SDK消息的每一个其它字段。
- 历史聚合仍只能看到 `device_gateway` 实际上报的连接状态，不是原始CAN采样。如果单帧CAN读取警告未被SDK映射成 `is_connected=false`，聚合可能仍全程22/22；不能据此否认日志警告或直接判定硬件健康/故障。
- 原始和重定向话题分别输出；未 patch 的固件通常只在原始话题有数据。writer 已匹配不等于手柄事件完整。手柄是事件驱动的，静止没新消息不能判掉线；断连也必须看实际事件与设备状态，不能发明固定心跳门槛。

**基线观察不要求移动摇杆或按任何激活键。** 诊断不发控制，不代表原厂会忽略现场按键：WALK、F1长按、组合键可能改变原厂模式。任何按键验证必须先由现场操作员确认安全状态和保护条件，不能为了采样激活站立、行走、踢球或悬空运行学习策略。

这个工具只能验证接收、类型/字段、发现与时序，不能证明 B-Human 执行器急停、遥控独占或双控制栈交接安全。不得以其成功为由直接启动 `bhuman`。后续交接必须单独验收。

## 控制话题隔离的离线准备

另外的 `controller_isolation.py` 只核验原件副本并生成固定三处等长变更、manifest与私有原件回滚副本，不提供真实固件应用操作。请先读 [CONTROLLER_ISOLATION.md](CONTROLLER_ISOLATION.md)，不能把 prepare/check/restore 当作已完成真机隔离或运动许可。
