# LiteHelioviewer

[English](README.md) | 中文

一个轻量的本地网页版太阳图像浏览器，数据来自 Helioviewer API。想法源于 [JHelioviewer-SWHV](https://github.com/Helioviewer-Project/JHelioviewer-SWHV)——那个项目很成熟，但日常只想快速看一眼太阳时有点重。LiteHelioviewer 保留了最常用的流程：加载 SDO/HMI、AIA、LASCO 近期的图，叠加图层，转动日面；又加上了我一直想要的小功能：圈一块区域细看，在上面画线分析。除了下载图像本身，所有东西都在本地运行。

## 快速开始

Windows：双击 `run-litehelioviewer.bat`。它会自动找到 Python 3.9+，首次运行时安装依赖，然后启动后端并打开浏览器。

其他系统：

```
pip install -r requirements.txt
python start.py
```

## 基本用法

选好图层、日期和时间，加载。鼠标拖动转动日面，滚轮缩放。也可以把本地 FITS 文件拖进窗口，或者叠加 PFSS 磁力线。

点 **Crop** 后在日面上拖出两个点圈定区域，每个区域会在下方以 CEA 投影的子图打开，坐标轴单位是千米；子图可以缩放、拖动，方便查看细节。在 Crop 模式下点击绿色区域可以选中它，重新拖动两个角点调整。

子图上可以画分析线，手绘或者贝塞尔曲线。每条线可以设置宽度和横向的高斯加权，点绘图按钮会生成沿线的拉直条带和对应的强度曲线。

## 近期更新

- 子图可以导出为干净的 PNG/JPG 图像，画的线也一并导出。
- 线的位置改为锚定在卡灵顿经纬度上：调整 crop 区域只是移动观察窗口，线在太阳上的位置不再跟着变。
- 线条可以选颜色，有色盘，也可以自定义取色。

## 计划

固定卡灵顿坐标的 crop 区域时序可视化。
