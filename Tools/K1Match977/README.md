# 977 helper纯源码

这是977已核启动层的配套构建输入，不含ELF、SDK或本机运行证明；不能在另一台机器人直接运行。

- `helpers/mode_query.cpp`：只读GetMode，绑定977 SoC1424225066425，8秒进程上限。
- `helpers/query_policy.h`及测试：无SDK的参数、身份和有界查询策略。
- `helpers/damping_only.cpp`：明确有动作的故障回退，只允许显式`--force-damping-only`切卸力；不是只读探针，不自动执行。
- `k1_dds_observer`：复用`Tools/K1Commissioning/dds_observer.cpp`构建，订阅观察，不把订阅健康当身体测试。

`bash Tools/K1Match977/build_helpers.sh <absolute-repository-root> <absolute-build-directory-outside-repository>`只在构建机编译，
要求合法获取的Booster SDK及已有交叉工具链位于官方约定目录，编译器/CMake/Ninja在PATH中。
它只运行无SDK的CPU策略测试，绝不执行生成的ARM程序、不SSH、不安装、不设置模式。
更换工具链/依赖后helper SHA可能变化，必须重新生成完整同版发行pin，不盲用公开参考SHA。

不会构建或复制ORT/TensorRT缓存；真实相机、路由权限、模型与本机缓存校验仍是独立发布门禁。
