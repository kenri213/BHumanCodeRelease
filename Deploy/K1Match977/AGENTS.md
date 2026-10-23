# 977官方F1常驻发行参考

先完整阅读本目录README及`Docs/K1_977_OFFICIAL_F1_RESIDENT_20261024.md`。
这些脚本是已冻结977专属参考，不是通用部署器。裸源码没有APP/helper/路由bundle/live元数据或本机cache证明，不能直接start或安装。

- 生产四脚本须保持与BUILD_INFO中的参考SHA一致；新构建需要新发行闭链，不能删pin、身份/权限检查或修改正常原件权限以过门禁。
- F1只切Emergency/Damping/Unstiff相关模式，APP驻留；单X没有新增全局退出，RB+X保留官方映射。显式stop才退出整个项目。
- 不迁入Pickup、BodyGap、头隔离、新low-state接收线程、AUTO断联例外、策略或开机预加载；其它旧工具scope不变。
- 保留每机Config、模型、PD、相机标定、SDK与缓存，禁止跨机器人复制证明。通用89/3/Default不等于977的114/1/SField。
- 文件发布不授权SSH/安装/启停/身体测试。不得因本公开提交连接机器人或自动起身。
- 不提交任何凭据、个人路径原始报告、ELF/厂商库/原路由备份、引擎cache、实机日志或系统备份。
- 回归只做纯CPU/临时文件测试，不在测试中调用生产LinuxOps/SDK。保留严格原断言，仅对路径fixture做精确移植。
