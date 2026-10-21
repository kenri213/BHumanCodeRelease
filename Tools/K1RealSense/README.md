# D455 被动诊断与有限采集工具

此工具只链接 librealsense 与 C++ 标准库，不包含 Booster SDK、ROS、运动发布器、模式切换、X5 或安装脚本。默认只枚举相机、USB、相机固件、目标流和实际内参，并检查彩色/深度组合能否解析，**不启动采集、不修改曝光或发射器选项**。它不会停止其他程序或解决 USB 占用。

## 编译（Linux ARM64）

```bash
bash Tools/K1RealSense/build_d455_probe.sh /tmp/bhuman_d455_probe
```

脚本只编译，不执行，不连接其他设备。优先使用目标机已有 pkg-config realsense2，其次使用已有 /usr/local SDK，最后回退仓库现有 Linux ARM64 librealsense 2.55.1；不安装或覆盖 SDK。SDK ABI、USB 权限与依赖仍以目标机实际环境为准。

## 默认被动检查

```bash
/tmp/bhuman_d455_probe
/tmp/bhuman_d455_probe --serial YOUR_CAMERA_SERIAL
```

目标是彩色 640×480 YUYV 30Hz，加原生辅助深度 424×240 Z16 5Hz。`--color-only` 排除深度组合；`--all-profiles` 输出完整流清单。多相机必须指定序列号。`CONFIG_CAN_RESOLVE=1` 不能证明相机当前空闲，更不是实际采集成功。

## 显式有限采集（另需授权）

```bash
/tmp/bhuman_d455_probe --serial YOUR_CAMERA_SERIAL --capture --frames 30 --timeout-ms 5000
```

只打开指定相机，最多 300 帧、采集等待最多 30 秒；默认 30 帧、5 秒。成功、异常、Ctrl+C/SIGTERM 均尝试正常停止本工具自己的相机流。SDK 打开设备操作本身可能受驱动影响，严格总时限可由操作者另加系统 timeout，但不要使用 SIGKILL。

输出验证实际 COLOR YUYV/尺寸/stride/数据长度/新帧编号，打印时间戳域、arrival 元数据及内参。不会保存图像，不修改相机设置，不启动机器人运动；深度只用于保持与原配置一致，不参与测距。出现 busy、权限或不支持的配置时退出报告，不杀进程、不自动降级到其他相机。

## 本地纯软件检查

```bash
clang++ -std=c++17 -Wall -Wextra -Werror -pedantic Tools/K1RealSense/diagnostic_options_test.cpp -o /tmp/d455_options_test
/tmp/d455_options_test
clang++ -std=c++17 -Wall -Wextra -Werror -pedantic -fsyntax-only -IUtil/librealsense2/include Tools/K1RealSense/d455_probe.cpp
```

这些不能代替目标机编译、实际 USB 采集和标定，也不证明 B-Human 整体部署或固件运动兼容。
