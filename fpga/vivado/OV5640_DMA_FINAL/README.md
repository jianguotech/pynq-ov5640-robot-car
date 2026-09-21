# OV5640_DMA_FINAL Vivado 快照

该目录来自 Windows 工作站上的 `D:\Vitis\OV5640_DMA_FINAL`，对应 Vivado 2023.2、PYNQ-Z1 (`xc7z020clg400-1`) 的 DVP + SCCB + AXI DMA 设计。

## 内容

- `OV5640_DVP_HW.xpr`：工程入口，外部 IOBUF/XDC 路径已改为仓库内相对路径。
- `OV5640_DVP_HW.srcs`：Block Design 和各 IP 的 XCI 配置。
- `exact-ip-src`：从本次工程生成目录 `ipshared` 中提取，和随附 bitstream 对应。
- `latest-windows-src`：Windows 工作区中更新较晚的 DVP 源码，仅用于继续开发和比较。
- `constraints`：PYNQ-Z1 摄像头引脚约束。
- `output`：同次实现生成的 bitstream 和 HWH。

## 版本边界

`exact-ip-src` 与 `latest-windows-src` 不能混用。后者增加或修改了 AXI-Stream/TLAST、CDC 和捕获逻辑，但没有证据表明随附 bitstream 使用了这些更新。

原工程引用的 `xilinx.com:user:myOV5640_DVP:1.0` IP 包在 Windows IP 仓库中已经缺失，因此这里保留精确 RTL 和生成的组件描述 `generated-component.xml` 作为恢复依据。重新综合前需要在 Vivado 中重新打包该 RTL，或使用 `fpga/ip/myOV5640_DVP` 的 clean 版本并重新校验 Block Design 接口。

## 外部文件

工程中的两个 module reference 已指向：

- `latest-windows-src/sda_iobuf.v`
- `latest-windows-src/scl_obuft.v`

约束文件已指向 `constraints/pynq_z1_ov5640_dvp_hw.xdc`。工程不再依赖原 Windows 用户目录。
