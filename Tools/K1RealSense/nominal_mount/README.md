# K1/D455 厂商名义安装初值（非实测、非活动配置）

这里的两个文件是模板，放在 Tools 下不会被 B-Human 自动加载；不修改 Default/X5/T1 或任何机器人的活动配置。经另行授权，可将其作为指定机器人私有 `Config/Robots/robot/` 的**名义初值**。若已有实测标定，先比较并备份，不应覆盖。

依据：Booster 1.7.1.1 包内 `BoosterAutoHandeyeCalib/.../K1_22dof.urdf` 的 `head_realsense_rgb_joint`：RGB光心相对head_pitch的xyz为 `(0.053617,-0.0115,0.10231)`米，rpy为 `(-1.835388,0,-1.570796)`弧度。不是深度光心或外壳中心。

RealSense optical轴是右、下、前，B-Human相机轴是前、左、上。输入轴变换后，名义相机旋转为 `Ry(-pi/2 - (-1.835388)) = Ry(+15.15998616895714°)`；正号表示向下。厂商yaw小数截断造成的约0.000019°残差不是实际标定精度。

B-Human链为 `T(hipToNeck) Rz(headYaw) Ry(headPitch) T(camera位置) Ry(camera.y) Rx(camera.x) Rz(camera.z)`。髋Pitch在trunk下77mm、Yaw在trunk上214.9mm、Pitch又高33mm，总高324.9mm。33mm沿Yaw轴可交换并折入hipToNeck；不能再加到相机z中，否则随Pitch错误旋转。

验证仅为数学等价：

```bash
python3 -B Tests/K1RealSense/test_nominal_mount.py
```

这不是996实际外参测量，不验证编码器零偏、身体倾斜、机械安装、传感时间对齐、运动、测距或定位。旧vision.yaml的20° yaw补偿不能自动继承。后续实测应使用正确去Inverse-Brown畸变的RGB图像及对应内参；已校正图像的PnP使用零畸变系数，不能重复套原Inverse系数。
