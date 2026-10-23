# 987已验证版本的启动层源码快照

此目录与987最小原生回场v4实装对应，身份、目录和各项pin是987专属；不覆盖`Deploy/K1Match996`。
`match_control.py`、`route_transaction.py`、`start_match.sh`与`README_MATCH.md`逐字对应本次实装版本。

用户在2026-10-24反馈Pickup后回场测试通过；证据类型、APP哈希与构建/发行验证范围见`RELEASE.json`及`Docs/K1_987_NATIVE_REENTRY_V4_20261024.md`。

这不是完整的通用安装包。厂商输入路由原件/补丁二进制、SDK、ELF、模型缓存、本机运行证明及真实路由manifest没有提交；权限测试从纯合成元数据构造临时manifest，不能把测试数据用作真机批准依据。克隆源码不等于完成固件、依赖、缓存或身体部署，也不应在另一台机器人直接运行本机入口。

离线控制器/权限测试只使用模拟操作和临时目录，不连接机器人：

```bash
python3 -m unittest discover -s Deploy/K1Match987 -v
```

历史文件安装回执保留私有原件。本目录不发布当时的boot状态、完整文件清单或一次性安装脚本；不要重跑绑定旧APP和旧boot的安装器。
