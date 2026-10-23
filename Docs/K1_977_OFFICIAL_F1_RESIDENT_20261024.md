# 977官方F1常驻模式：源码发布与验收边界

本分支以公开`35b4d3cee3e8f3d8e8774a01f73cefde44ee0fd4`为基座，
它对应996实装`37a2c4ba`的恢复/断联Stand逻辑。本轮按977已部署c629发行冻结源码发布，
不是基于后续Pickup分支，也不把其它机器人后来的功能打包进来。

完整可审查输入由本仓库的完整基座加本提交增量组成，不需要私有开发机路径。
保留上游许可证和原有通用工具；不新增认证凭据、本机密钥、SDK二进制、模型引擎缓存或实机日志。
上游历史已有默认安装钥匙及profiles仍保留，不据此宣称整个Git历史从未包含钥匙。

## 核心变化

1. 仅在977准确SN/目录/CLI入口启用官方模式语义；旧996等入口不变。
2. 实体F1进入Emergency/Damping不再终止APP；同一APP长按F1松开回Unstiff，不自动Prepare/GetUp。
3. 移除自建“单X全局退出APP”；RB+X保留原官方KeyStates::f1/unstiff映射，不发明新的X模式。
4. 显式SIGINT/SIGTERM或安装入口`stop`仍真正退出并走自己的资源恢复；没有固定比赛运行截止。
5. 原授权后的传感器、模式、输出/PD、姿态与裁判保护继续保留。首启连接仍有独立目的的原握手，
   F1可打断该目的但不会因此退出整个项目；本977不额外增加Cognition帧数/500ms授权门槛。

必要的D455、977身份、唯一裁判来源、实体RB覆盖和RB+A合并Prepare/起身是已说明的本地适配，
不能称为与官方代码逐字完全一致。共享白线无法单独证明半场/朝向；本轮不取消正确摆位和视觉确认的责任。

## 没有顺便改的部分

- 基座完整生产Src除Main、BoosterOfficialLiveTest、BoosterProvider、GameStateProvider外保持。
- Config、20模型、PD、运动与踢球策略、名义相机参数、Make和Install工具未改。
- 不含新Pickup/PRE/WALK回场、BodyGap、新low-state接收线程、头隔离、AUTO断联例外或新的进攻策略。
- 普通手柄断联仍停步锁Stand，不续旧摇杆，之后需要新中立及明确交权。
- 没有开机预加载或启动提速；未声称本版已部署或验证`-w`。
- 没有修复或排除原厂CAN/头部通信硬件风险；模式语义修复不等于永不倒地。

## 发布目录

- `Src/`及`Tests/`：完整基座上的冻结4个生产增量、相关回归；977身份和旧scope有独立负例。
- `Deploy/K1Match977/`：生产四脚本精确字节、六项离线测试、空元数据模板、脱敏配置示例及说明。
- `Tools/K1Match977/`：977专属query/damping helper纯源码及交叉构建入口，不附产物。
- `Deploy/K1Match977/BUILD_INFO.json`：公开构建摘要、参考ELF和原私有报告指纹，原报告本身不公开。
- `Deploy/K1Match977/PUBLIC_SOURCE_SHA256.json`：全部985个Src的公开SHA清单，已逐项对照私有冻结报告；
  其中4个本轮生产文件及981个其它Src均保持冻结字节，不依赖开发机绝对路径。
- `Deploy/K1Match996/`及其它旧工具保留旧scope，不代表它们已同步977新按键语义。

`Config/settings.cfg`保留仓库通用89队/3号/Default；实际977是114队/1号守门员/SField约9×6米/
K1RealSense/仅192.168.50.37。不能将整个仓库Config覆盖到真机；脱敏example不会自动加载，
专属相机标定、真实D455序号及缓存必须在同一目标机独立取证，不借996或其它机器的材料。

## 离线测试

在本仓库根目录执行，不需要SSH、SDK进程或机器人：

```bash
python3 -m unittest discover -s Tests/K1Commissioning -p 'test_*.py'
python3 -m unittest discover -s Tests/K1RealSense -p 'test_*.py'
python3 -m unittest discover -s Deploy/K1Match977 -p 'test_*.py'
```

冻结开发记录为277项source、41项相机、146项启动器/更新器通过，另31项专项回放。
专项与总集合可能重叠，不将相加数字解释成独立场景或物理测试次数。
本公开检出副本已重新通过286项source、41项相机、146项启动层测试和13项helper CPU策略检查。
其中9项可移植生产片段回放属于286项集合，不重复相加；所有985份生产Src均与已实装参考逐字匹配。
本次发布没有重新做ARM全编译，也没有连接、启动或修改机器人；沿用并明确区分此前冻结构建与真机F1证据。
测试基线读取本分支祖先的固定公开Git对象；缺失时必须失败，不通过删除断言或skip迎合记录。
本次公开检出复测为286项Commissioning（包含可移植回放）、41项RealSense、146项启动层通过，
另SDK无关helper策略13项检查通过；它们依然不是身体比赛验收。

## 构建与同版闭链

参考构建为Clang19.1.7、Develop、Linux/AArch64、Ninja；须从合法来源准备官方工具链和匹配依赖。
RealSense SDK的977已核参考SHA为`7308f5ef76a302d803d435975acf7772814c631dfd952077af2a43d0f816d169`，
这里只记录指纹，不附该库。其它SDK/ORT依赖也不是本次新发布的载荷。

在已有兼容交叉依赖的构建机，使用当前检出根目录与独立构建目录，例如：

```bash
cmake -S <repository>/Make/CMake -B <outside-build> -G Ninja \
  -DBUILD_BOOSTER=ON -DCMAKE_BUILD_TYPE=Develop \
  -DCMAKE_TOOLCHAIN_FILE=<repository>/Make/CMake/BoosterToolchain.cmake
cmake --build <outside-build> --target Booster --parallel 2
```

项目本身仍将Booster产物写入忽略的`Build/Linux/Booster/Develop`，不能提交Build或ELF。
helper构建命令见`Tools/K1Match977/README.md`；脚本不会执行ARM产物。
重新构建的路径/编译器/依赖差异可能改变ELF SHA，因此c629是已安装参考，不保证新ELF逐字相同。
新产物必须重新冻结APP/helper与bundle→core→controller→发行/验证清单，不能盲沿旧pin或删除门禁。

公开目录没有APP、路由bundle、本机cache证明或批准live元数据，**不能直接运行/部署**。
参考`install_update.py`只接受已核977旧2cfa发行的精确同项目更新，不是首次安装器；已装c629时也不得重跑。
需要独立核SN、服务/APP已停止、原路由完整且实际uid/gid/mode/bytes正确、备份旧版，再考虑明确授权的发布流程。
裸源码、模板或本公开提交不授予刷固件、停启服务、设置模式、自动起身或比赛权限。

## 真机证据与未测部分

977已完成本版文件校验与冷启新鲜Cognition，并验证真实实体F1模式0→3→0时保持同一APP PID。
这只证明驻留模式切换，不是起身、走路、踢球或完整比赛通过；未做本版这些物理验收。
验证后程序已停止，本次源码发布不连接机器人、不启动APP或进行模式切换。
原始私有日志/安装回执/系统备份继续私下保存，公开摘要不伪装成那些原始字节。

代码回退与机器人文件/路由回退是两件事；Git分支回退不会自动恢复已安装文件。
任何真机恢复都须使用该机器已核备份，在安全停止与原路由状态确认后明确授权执行。
