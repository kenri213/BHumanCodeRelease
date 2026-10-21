# 控制输入隔离：只准备私有副本，不修改真实固件

> 当前方案改为**严格官方两处patch的离线准备**：gateway只改按钮话题，其remote state原样保留；只有remote库改到stat2。**尚未真机应用或验证，不能称为已修复手柄问题。**历史三处方案的bundle、原件、manifest和哈希必须原封保留，不得改写成新方案；旧bundle须使用对应冻结旧工具/恢复策略。本工具现在明确拒绝历史三处manifest。

`controller_isolation.py` 没有 live apply 功能，不执行外部命令、服务、网络或机器人接口。所有源必须是事先取回的原件副本；源和输出都要求规范绝对私有路径，不允许系统目录、符号链接、非普通文件、覆盖已有目录或路径漂移。使用 `/private/tmp/...` 而不是带父目录符号链接的 `/tmp/...`。

## 当前：两份原件、两处等长变更

在996升级后的1.7.1.1实测拓扑中，gateway和remote_controller都发布原始remote state。历史三处方案将两者同时搬进stat2，使官方PS4通道仍可有两个writer。Provider会逐包覆盖完整手柄状态；两个源若有不同的neutral/pressed状态，存在打断持续按键的风险，但**尚未证明它就是本次手势无效的根因**。

新的离线准备方案与官方安装脚本两处替换对齐，避免将gateway remote源一并搬入stat2。它不修改SDK回调、官方按键映射、PD或模型，也不自行合并两个源的按钮。gateway保持原remote话题后，原厂消费者是否会响应仍须另行核验，不能由prepare推出控制独占已通过。

准备器固定且仅允许：

| 原件副本 | 等长替换 |
| --- | --- |
| `device_gateway` | `rt/button_event` → `rt/button_even2` |
| `libremote_controller.so` | `rt/remote_controller_state` → `rt/remote_controller_stat2` |

上述每个待替换old必须恰好1次、new必须0次。另独立要求gateway中的`rt/remote_controller_state`恰好1次、`rt/remote_controller_stat2`为0次，准备后保留原字节和offset；不能拿已搬过remote的gateway当新方案源。ELF需为64位、小端AArch64。必须人工提供两份已独立核对的完整SHA-256；工具不带机器人专属默认hash，不自动接受新固件。若计数/哈希不符，停止审查，不能扩大替换范围。

## 准备

以下只是离线参数格式，不能把占位文字直接执行：

```text
python3 Tools/K1Commissioning/controller_isolation.py prepare \
  --gateway-source <原件副本绝对路径> --gateway-sha256 <人工确认的64位小写SHA256> \
  --remote-library-source <库原件副本绝对路径> --remote-library-sha256 <人工确认的64位小写SHA256> \
  --output-dir <尚不存在的私有目录绝对路径>
```

输出包含 `originals/` 两份原件、`patched/` 两份机械修改副本、`manifest.json`、`manifest.sha256`。目录0700、所有产物0600（包括ELF，故不可直接执行）；原始权限仅记录供人工审核，不应用。manifest以`routing_policy=official_two_patch_remote_library_only`和`patch_location_count=2`标识方案，并逐处记录字串、offset、变化字节位置、整文件哈希/长度、原件身份和回滚信息。

保存终端返回的 `manifest_sha256` 到独立可信记录。旁边的sidecar只是辅助，不能代替这个外部校验值，也不是数字签名。工具重复核对源文件内容、inode/时间与父目录身份，写入用no-follow目录FD锚定；失败只清理自己创建且inode未变的私有条目，绝不递归清理已有目录。

## 检查与私有回滚副本

```text
python3 Tools/K1Commissioning/controller_isolation.py check \
  --bundle-dir <准备目录> --manifest-sha256 <独立记录的manifest哈希>

python3 Tools/K1Commissioning/controller_isolation.py restore \
  --bundle-dir <准备目录> --manifest-sha256 <独立记录的manifest哈希> \
  --output-dir <新的私有原件输出目录>
```

`check`验证两份完整原件、两个修改副本、固定两处机械变更、gateway remote原样保留及路径/权限，不接受额外字节变化。`restore` **只把已验真的原件放入新的私有目录**，同样0600；不会回写`/opt/booster`或改变执行权限、进程、服务。新bundle不能直接套用历史三处live恢复器的固定策略；必须另行审核新policy/hash，当前工具仍无live功能。

## 不能由prepare推出的结论

等长变更通过只证明两处字节变化符合白名单，不证明运行语义、急停、遥控独占或SDK交接通过。真实切换必须另行审查、授权、记录原厂进程和服务，不能在运行中替换库并假定已生效；现有进程可能仍映射旧文件。此工具不提供该操作。

## 无设备回归

```bash
python3 -B Tests/K1Commissioning/test_controller_isolation.py
```

全部使用临时目录中的合成ELF，不接触真实原件、固件、服务或机器人。保留坏哈希、重复/缺失字串、已有new、架构、符号链接/FIFO、已有目录、源/父目录漂移、中途写失败、篡改及私有恢复测试；新增gateway remote保留、库独立stat2、拒绝已搬移gateway源、拒绝重新解释历史三处bundle。
