---
name: litehelioviewer-control
description: 控制本机 LiteHelioviewer 进行轻量太阳观测可视化。用户要求用 litehelioviewer 打开/画/显示 SDO HMI 磁图、HMI 连续谱、AIA 波段、SOHO LASCO C2/C3、打开本地 FITS、叠加图层、调 opacity、打开 PFSS 线条层时使用此技能。
---

# litehelioviewer-control

## 固定路径

- 软件目录：`G:\softwares\litehelioviewer`
- 启动脚本：`G:\softwares\litehelioviewer\run-litehelioviewer.bat`
- Python：`G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe`
- Web 地址：`http://127.0.0.1:8765`
- 数据目录：`G:\softwares\litehelioviewer\data`
- 缓存 manifest：`G:\softwares\litehelioviewer\data\cache\manifest.json`

始终在软件目录运行命令：

```powershell
cd "G:\softwares\litehelioviewer"
```

## 启动

如果 LiteHelioviewer 没开，执行：

```powershell
Start-Process "G:\softwares\litehelioviewer\run-litehelioviewer.bat"
```

这个 bat 会自动打开浏览器：

```text
http://127.0.0.1:8765
```

不要隐藏这个窗口；该窗口就是后端日志窗口。用户关闭这个窗口时，由它启动的后端会停止。

## 检查后台是否运行

```powershell
cd "G:\softwares\litehelioviewer"
.\status-litehelioviewer.bat
```

或直接查端口：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
```

## 常用命令

### HMI 全日面磁图

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load hmi-magnetogram --date 2013-02-15T12:00:00 --opacity 1
```

### HMI 连续谱/黑子图

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load hmi-continuum --date 2013-02-15T12:00:00 --opacity 1
```

### AIA 波段图

把 `aia-171` 换成用户要求的波段：

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load aia-171 --date 2013-02-15T12:00:00 --opacity 1
```

可用 preset：

```text
aia-94, aia-131, aia-171, aia-193, aia-211, aia-304, aia-335, aia-1600, aia-1700, aia-4500
```

### LASCO C2/C3

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load lasco-c2 --date 2013-02-15T12:00:00 --opacity 1
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load lasco-c3 --date 2013-02-15T12:00:00 --opacity 1
```

### HMI + AIA 叠加

先加 HMI，再加 AIA，并把第二层 opacity 设小：

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load hmi-magnetogram --date 2013-02-15T12:00:00 --opacity 1
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli load aia-171 --date 2013-02-15T12:00:00 --opacity 0.35
```

### 打开本地 FITS

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli fits "G:\path\to\file.fits" --opacity 1
```

用户也可以把 `.fits`、`.fit`、`.fts` 文件直接拖到浏览器里的太阳视图区域。

### PFSS 线条层

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli pfss --date 2013-02-15T12:00:00
```

PFSS 会从 `https://swhv.oma.be/pfss/` 读取官方月列表，选择离请求时间最近的 PFSS FITS，解码 `FIELDLINEx/y/z/s` 并按 JHelioviewer 的 Earth longitude 约定旋转后显示。浏览器按钮会优先使用当前可见图层的时间和 Carrington 中心经度；CLI 如需更严谨对齐，可加 `--central-lon`。

### 清空图层

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli clear
```

### 查看图层

```powershell
& "G:\python_projects\envs\WPy64-31241\python-3.12.4.amd64\python.exe" -m litehelioviewer_app.cli layers
```

## 时间规则

- 用户给“某时”：直接用 UTC `YYYY-MM-DDTHH:MM:SS`。
- 用户没说时区：按 UTC。
- 用户只给日期：补 `T00:00:00`。

## 数据源规则

默认服务器用 `GSFC`。如果用户明确要求 IAS：

```powershell
--server IAS
```

常用映射：

| 用户说法 | preset |
|---|---|
| HMI 磁图、全日面磁图、magnetogram | `hmi-magnetogram` |
| HMI 连续谱、黑子图、continuum | `hmi-continuum` |
| AIA 171 | `aia-171` |
| AIA 193 | `aia-193` |
| AIA 304 | `aia-304` |
| LASCO C2 | `lasco-c2` |
| LASCO C3 | `lasco-c3` |

## 缓存和颜色规则

- LiteHelioviewer 会先查 `getClosestImage`，确定最近实际观测时间，再用 `getJP2Image` 下载 JP2。
- 缓存文件名包含 server、source id、preset 和最近实际时间，例如 `hv_ias_sid10_aia-171_20130215_115959_sdo_aia_171_a.png`。
- AIA 默认使用 JHelioviewer 自带 `SDO-AIA ...` LUT，不是灰度。
- HMI 磁图和连续谱默认灰度。
- LASCO C2 使用 `Red Temperature`，LASCO C3 使用 `Blue/White Linear`。
- 页面左侧 Log 区会保留下载、成功和错误信息；优先让用户看 Log 区，不要只引用左上角状态。

## 不要误报

- CLI 成功表示图层已加入 LiteHelioviewer 后端；用户仍需在浏览器窗口查看画面。
- 下载的 PNG 和 FITS 转换图会放在 `G:\softwares\litehelioviewer\data`。
- 当前版本不是 JHelioviewer GUI 的瘦身 Java fork，而是独立轻量本地 Web 应用。
