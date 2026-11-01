# 开发、打包与验证

[返回首页](../README.md) · [使用说明](../使用说明.md) · [常见问题](FAQ.md)

## 环境

项目面向 Windows x64，源码环境使用 Python 3.13 与根 `requirements.txt` 的固定版本。使用隔离环境，不向系统 Python 直接安装整套桌面依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

依赖包括 PySide6、NumPy、SciPy、sounddevice、SoundFile 和 PyInstaller。启动应用会使用音频输入；只需查看算法时，不必打开界面。

## 模块

| 文件 | 职责 |
| --- | --- |
| `app.py` / `ui_theme.py` | 实时窗口与样式 |
| `pitch_core.py` | 无 UI 的单音 YIN 检测及音名换算 |
| `audio_engine.py` / `audio_platform.py` | 实时音频处理与平台接口 |
| `device_catalog.py` | 输入设备列表和旧设置匹配 |
| `recording_analysis.py` / `recording_audio.py` | 离线轨迹与回放 |
| `recording_window.py` / `piano_roll.py` | 录音复盘及卷帘 |
| `tonality.py` | 调性估计 |

## 分层验证

仅需要 NumPy 的算法检查：

```powershell
python -m unittest test_pitch test_tonality -v
```

本次 Python 3.13 环境中这 6 项通过，使用合成音高和调性数据，不打开麦克风。该环境没有完整 Qt / 音频开发依赖，因此本次未运行下面的完整音频与界面检查。

安装 requirements 后，可按首页执行设备、录音、音频管线和界面验证。GUI 验证会打开窗口；`verify_audio_hardware.py` 明确访问当前真实输入设备，仅在需要硬件检查时主动运行，不作为无声自动测试。

合成算法结果不能代表真人演唱、伴奏混合、声卡时钟或所有麦克风的准确度。对单音、音区与音分显示的限制见使用说明。

## 打包

```powershell
.\build.ps1 -Portable
.\.venv\Scripts\python.exe package_release.py
```

公开便携版需保留 `_internal`、第三方许可与源码获取说明。`build.ps1` 的单文件模式和便携目录模式不同，不把二者的分发内容混用。本轮未重新制作安装或发布包。

设置与录音位于 `%LOCALAPPDATA%\VocalPitch`。源码目录的 verification 是本机测试生成目录，不保证随公开仓库存在；可通过相应测试重新生成，不能把历史本机截图路径当作公开下载。
