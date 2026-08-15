# Bilibili Subtitles

将普通 B 站 BV 视频已有字幕提取为本地 Markdown。工具只读取字幕和视频元数据，不下载视频或音频。

## 支持范围

- 仅接受 `https://www.bilibili.com/video/BV...` 或 `https://bilibili.com/video/BV...` 形式的完整 HTTPS 链接。
- 默认处理视频的全部分 P。
- 字幕优先级为 `zh-Hans`、其他中文（包括 `ai-zh`）、首个其他非弹幕字幕。
- 只提取 B 站已经提供的字幕，不对无字幕视频进行语音转写。
- 不支持 `b23.tv` 短链接、番剧链接或绕过登录、会员、付费和地区限制。
- 交互视频不是普通分 P，第一版会明确拒绝；旧式分段媒体会合并为一个逻辑视频处理。

## 环境

- Windows PowerShell 5.1 或更高版本。
- Python 3.11，由 Windows `py` 启动器提供。
- Chrome 登录状态可选。工具优先临时读取 Chrome Cookie；读取失败时会明确提示并匿名重试，不保存或输出 Cookie。

## 安装

在本目录运行一次：

```powershell
.\setup.ps1
```

脚本在 `.venv\` 创建隔离环境，安装 `requirements.txt` 中固定版本的 yt-dlp，并运行测试。

## 使用

```powershell
.\extract.ps1 "https://www.bilibili.com/video/BVxxxxxxxxxx"
```

也可指定其他本地输出根目录：

```powershell
.\extract.ps1 "https://bilibili.com/video/BVxxxxxxxxxx?p=2" -OutputRoot "D:\subtitles"
```

公开视频可显式禁止读取浏览器 Cookie；工具会匿名读取视频元数据，并在需要时从 B 站公开字幕接口补充已有字幕：

```powershell
.\extract.ps1 "https://www.bilibili.com/video/BVxxxxxxxxxx" -NoBrowserCookies
```

输入中的 `p` 和其他查询参数仅用于识别链接，提取时会规范化为 BV 主链接并处理全部分 P。`www.bilibili.com` 和裸域名 `bilibili.com` 均可使用。

输出位于 `output\<BV号>\`：

- `index.md`：各分 P 的提取状态和链接；
- `part-001.md` 等：元数据和 `[HH:MM:SS]` 格式的字幕正文。

若部分分 P 无字幕，其他分 P 仍会生成；若全部分 P 都无字幕，命令返回失败且保留上一次成功输出。每次成功运行先完整生成临时目录再整体切换旧结果；普通异常会立即回滚，中断留下的备份会在下次运行开始时恢复，同一 BV 的并发运行会被拒绝。Windows 无法把两个非空目录作为单一步骤替换，因此进程在两个重命名之间被强制终止时，固定输出路径会暂时缺失，直至下次运行执行恢复。

## 排错

- 提示先运行 `setup.ps1`：隔离环境尚未创建或已被删除。
- Chrome Cookie 读取失败：工具会匿名重试；若字幕仅登录可见，可关闭正在占用 Cookie 数据库的 Chrome 后重试。
- 关闭 Chrome 后仍提示 `Failed to decrypt with DPAPI`：这是 yt-dlp 记录的 [Windows/Chromium Cookie 解密问题](https://github.com/yt-dlp/yt-dlp/issues/10927)。工具仍会匿名重试，但无法读取仅登录可见的字幕；不要为绕过该问题把完整浏览器 Cookie 提交到仓库或第三方服务。
- 提示没有已有字幕：视频可能只有弹幕，且 B 站公开字幕接口也没有提供字幕。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

字幕文件仅用于你有权访问和使用的本地学习材料。引用内容前应回看原视频并遵守平台条款及相关权利限制。
