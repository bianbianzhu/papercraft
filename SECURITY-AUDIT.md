# Papercraft 仓库与技能安全审计

审计对象为 Papercraft 0.6.0，本地提交 `5eea4eb004727f03492cfb40309224e150475002`，审计日期为 2026-11-27。

在已审阅的源码、技能文档、图库和本地 Git 历史中，未发现已植入的恶意提示注入、后门、硬编码凭证或实际个人信息外传的证据。优先收紧截图流程对项目文件和出站网络的访问。3D 模板的 CDN 请求、安装器遥测和按 URL 读取视频属于不同的联网路径。

## 审计发现

本节保留修复前的发现，源码行号对应[基线提交](https://github.com/bianbianzhu/papercraft/tree/5eea4eb004727f03492cfb40309224e150475002)，不是当前工作区的定位。当前实现和验收进度见文末；不应把以下八项理解为八个仍未修复、已经证实可利用的漏洞。

### 高风险

1. **截图页面可访问项目目录。** `capture.py` 在回环地址启动 HTTP 服务，整个传入目录都在服务范围内。处理器支持目录列表，可提供隐藏文件，也会跟随符号链接。脚本用 Playwright 运行页面，未显式开启 Chromium 沙箱，也未限制出站。若服务范围含私有文件，页面内脚本可按同源权限读取，并在能联网的环境中发送。建议只提供预览所需文件，拒绝目录列表和软链接，启用沙箱并限制出站。证据见 [本地 HTTP 服务](skills/papercraft/scripts/capture.py#L68)、[浏览器启动](skills/papercraft/scripts/capture.py#L152)、[Python HTTP 安全说明](https://docs.python.org/3/library/http.server.html#security-considerations)和 [Playwright 默认沙箱设置](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-option-chromium-sandbox)。

### 中风险

2. **两套 3D 入口从 jsDelivr 加载并执行 Three.js。** 导入图固定到 `three@0.187.0`，页面没有核验交付字节的哈希。正常加载会发送连接和请求元数据。若交付模块受到篡改，模块以页面权限执行。建议校验依赖完整性，并提供本地副本供离线预览。仓库的 2D 模板使用本地模块。证据见 [3D 模板](skills/papercraft/assets/templates/popup-3d/index.html#L15)、[结构演示](skills/papercraft/assets/templates/popup-3d/structures.html#L15)和 [2D 模板](skills/papercraft/assets/templates/popup-2d/index.html#L15)。
3. **页面和参考素材可把指令带回代理上下文。** 截图脚本把页面控制台警告、错误和部分诊断字段打印给调用方。参考图、截图和评审回复也可能含有伪装成指令的内容。建议保留诊断能力，并对页面文本做长度限制、控制字符转义和不可信来源标注。在技能中说明素材与评审内容只有参考价值。证据见 [日志采集](skills/papercraft/scripts/capture.py#L172)、[日志输出](skills/papercraft/scripts/capture.py#L244)和 [评审流程](skills/papercraft/references/process.md#L309)。
4. **输出名称和镜头参数缺少边界校验。** 镜头名称和纹理 `--name` 直接进入输出路径，可能跳出预定目录。视频模式会删除目标目录中匹配约定名称的旧帧，编码允许覆盖输出。`spot` 的距离参数直接拼接到页面执行的 JavaScript。建议验证输出路径包含关系和名称，检查软链接，并将距离解析成有限数值后独立传给 Playwright。证据见 [镜头参数](skills/papercraft/scripts/capture.py#L77)、[旧帧清理](skills/papercraft/scripts/capture.py#L196)、[编码](skills/papercraft/scripts/capture.py#L128)和 [纹理导出](skills/papercraft/scripts/paper_textures.py#L737)。
5. **README 的安装和预览命令扩大网络暴露。** `npx skills`、`npx serve` 和 Playwright 安装说明未锁定依赖版本。Vercel Skills CLI 的公开实现默认可向 `www.skills.sh` 发送遥测和安全审计查询。README 的 Python 预览命令默认监听所有接口。建议使用已审计提交中的本地技能目录，或固定安装器及来源并明确遥测设置。人工预览应只提供构建目录并绑定回环地址。证据见 [安装说明](README.md#L29)、[预览说明](README.md#L77)、[截图依赖](skills/papercraft/scripts/capture.py#L40)、[Skills CLI 遥测源码](https://github.com/vercel-labs/skills/blob/main/src/telemetry.ts)和 [Python CLI 绑定行为](https://docs.python.org/3/library/http.server.html#command-line-interface)。
6. **视频测量允许 FFmpeg 直接使用输入 URL。** `measure.py` 按后缀识别视频，直接传给 `ffmpeg -i`，缺少本地文件校验、协议白名单和子进程超时。若代理透传远程视频地址，支持相应协议的 FFmpeg 会主动连接。建议将测量限定为本地常规文件并设置协议和超时。两处 FFmpeg 调用使用参数数组。证据见 [输入分类](skills/papercraft/scripts/measure.py#L768)、[视频解码](skills/papercraft/scripts/measure.py#L194)和 [FFmpeg 协议说明](https://ffmpeg.org/ffmpeg-protocols.html#Protocol-Options)。

### 低风险

7. **评审产物可能携带私有素材和文件路径。** A/B 拼图会包含参考图像素，答案 JSON 默认记录输入绝对路径和 SHA-256。技能建议记录用户输入，也允许在用户要求生成图像时把参考图交给图像工具。分享目录可能暴露素材、路径中的用户名或客户名、素材指纹。建议只分享已核对的单份产物，以相对路径或内部标识记录答案，并确认素材可以提供给所选工具。证据见 [拼图输入](skills/papercraft/scripts/ab_sheet.py#L132)、[答案文件](skills/papercraft/scripts/ab_sheet.py#L178)、[日志约定](skills/papercraft/references/process.md#L40)和 [图像工具指引](skills/papercraft/references/image-gen.md#L15)。
8. **渲染和编码缺少统一的资源上限。** 分辨率、超采样、时长和帧率会影响内存、GPU 和磁盘占用。自动化应给单次任务设置像素、总帧数、磁盘和运行时间预算，并覆盖现有标准输出尺寸。证据见 [尺寸解析](skills/papercraft/scripts/capture.py#L53)、[逐帧截图](skills/papercraft/scripts/capture.py#L202)和 [3D 超采样](skills/papercraft/assets/templates/popup-3d/harness.js#L105)。

## 范围与验证边界

- 只读静态审计覆盖 52 个跟踪文件、3 个本地提交、4 个 Python 脚本、12 个 JavaScript 文件和 11 张 JPEG。审计时工作树干净，未发现额外忽略文件、跟踪的软链接、子模块或启用的仓库钩子。
- 对当前和历史内容扫描了常见凭证、个人信息和隐蔽指令特征。提交元数据中核对到一个 `noreply` 邮箱。JPEG 的应用段只有标准 JFIF，未发现 EXIF、GPS 或图像结束标记后的数据，目视检查未见明显指令文字。
- 本地 `origin` 是 `bianbianzhu/papercraft`。README 指向的公开上游 `xikhar/papercraft` 包含与本地相同的完整提交 SHA。证据见 [上游提交](https://github.com/xikhar/papercraft/commit/5eea4eb004727f03492cfb40309224e150475002)。
- 结论基于源码和上游资料。审计未运行仓库脚本、启动预览服务或安装依赖。运行时流量、CDN 返回字节和本机第三方依赖版本未验证。

## Astra 独立复核与修复顺序

Astra 对同一提交做了独立静态复核。建议给渲染提供可校验的本地依赖，再收紧文件和网络边界。调整渲染依赖的交付方式时，应保留当前 r187。纸色阴影和软阴影会修改 Three.js 的着色器片段，换版本可能引起视觉退化，即使页面仍能正常启动。证据见 [纸色阴影](skills/papercraft/assets/lib/paperkit-3d.js#L66)和 [软阴影](skills/papercraft/assets/lib/paperkit-3d.js#L675)。

1. **先准备离线 3D 依赖。** 从固定版本的软件包获取并校验 `three.module.js` 和相邻的 `three.core.js`，保留 MIT 许可并记录文件哈希。两个 3D 页面共用这份本地依赖，仍通过原有 `three` 导入名引用。同步更新用户复制模板的说明。CDN 可供维护者显式获取已批准的依赖。依赖外部字体、素材或 API 的自定义项目需先把资源准备到本地，或为该项目单独配置允许的访问。证据见 [r187 入口模块](https://raw.githubusercontent.com/mrdoob/three.js/r187/build/three.module.js#L6)、[r187 核心模块](https://raw.githubusercontent.com/mrdoob/three.js/r187/build/three.core.js#L6)、[MIT 许可](https://raw.githubusercontent.com/mrdoob/three.js/r187/LICENSE)和 [自定义项目接入说明](skills/papercraft/references/three-js.md#L29)。
2. **再收紧预览范围。** 以只读方式服务经过选择的构建目录，保持 `assets/` 下现有相对布局。样例页面通过 `../../` 使用上级的 `lib/` 和 `examples/`，预览范围缩到单个模板目录会使导入失效。继续支持带尾斜杠的 `index.html` 路由、嵌套的 `--page` 及明确提供的本地图片、字体和数据。私有源图、评审文件和截图产物放在服务目录之外。Chrome 与备用 Chromium 应使用相同的沙箱要求，并在有凭证的环境外运行不可信页面。保留 WebGL 与 Windows ANGLE 配置并实测。用环境级出站隔离约束浏览器，Playwright 请求拦截用于补充审计，使用时还需处理 Service Worker 和 WebSocket。证据见 [2D 场景导入](skills/papercraft/assets/templates/popup-2d/scene.js#L7)、[3D 场景导入](skills/papercraft/assets/templates/popup-3d/scene.js#L4)、[浏览器启动参数](skills/papercraft/scripts/capture.py#L160)和 [Playwright 请求拦截](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)。
3. **保留 CLI、日志和评审工作流。** 校验显式与自动生成的镜头名，保留嵌套页面、命名镜头、有效查询选项和用户显式指定的输出目录。限定自动生成的文件必须位于选定输出目录。在独立临时目录写本次视频帧，保留缺少 FFmpeg 时的 PNG 输出。FFmpeg 视频测量限制本地输入，保持 PNG 管道输出。日志保留错误类型、计数和失败退出码，对页面返回的自由文本限长和转义。保留盲评拼图和私有映射、用户自愿使用图像工具的入口，以及现有的光影、纸张纹理、声音、场景和图库。证据见 [CLI 参数](skills/papercraft/scripts/capture.py#L141)、[视频输出分支](skills/papercraft/scripts/capture.py#L196)和 [视频测量](skills/papercraft/scripts/measure.py#L194)。

### 实施后的验收

- 预先在同一浏览器、操作系统、GPU 后端和固定时间点采集基线。切换本地依赖后检查 2D、3D 和结构演示均能断网启动，交互和软阴影正常，纸张纹理和构图与基线一致。补测一个含嵌套页面和本地素材的自定义项目。
- 验证横屏、竖屏、4K 和超采样，走通固定时钟截图、命名视频、路线视频、FFmpeg 测量和无 FFmpeg 的 PNG 输出。检查写入失败或重复运行是否会影响已有产物，并核查纹理和 A/B 导出。
- 用无真实秘密的测试目录核对服务范围和外部请求约束，确认页面错误依然能定位，合法文件名和文档中的常用参数可继续运行。

## 实施与验收进度（2026-11-27）

以上是原始审计与实施计划。下表记录本地实现和验收，不代表已经发布或合入上游。

| 原发现 | 当前实现 | 剩余边界 |
|---|---|---|
| 1：预览文件与网络 | `capture.py` 拒绝隐藏路径、软链接、目录列表和输出目录访问；检查 Host，设置 CSP；Chrome 和备用 Chromium 均请求沙箱；限制 HTTP 来源并阻断页面 WebSocket、Service Worker。 | 公开构建目录内的普通文件仍可读，不能混入私有素材。浏览器请求拦截不是 OS 级出站隔离；不可信页面仍需无凭据的隔离运行环境。 |
| 2：CDN 依赖 | 两个 3D 入口使用本地 r187；保留两个模块、MIT 许可、包完整性与 SHA-256 清单，并更新复制模板的指引。 | 属于供应链加固，不是已发现依赖投毒。升级依赖时需重新校验和验收。 |
| 3：不可信页面诊断 | 上下文保留限长、转义、标记不可信的摘要，明确省略数量；完整度更高的控制台消息、页面堆栈和场景诊断在容量预算内保存到私有 JSONL，支持按镜头读取。技能及评审流程明确素材不具备指令权限。 | 标记与转义不能消除语义层提示注入；详细日志仍是不可信、可能敏感的数据。超出日志预算的内容会被截断或省略，并明确标记。 |
| 4：输出路径和参数 | `output_paths.py` 统一名称与软链接检查；截图和纹理使用暂存文件；视频不再删除旧帧，重复名称保留既有产物；`spot` 距离是独立数值参数。 | 明确指定的输出目录仍由调用者选择；不宣称抵御拥有该目录写权限的并发本地攻击者。 |
| 5：安装与人工预览 | README 改为从已审阅版本本地复制，并使用回环地址预览，说明隔离前提。 | 未建立完整依赖锁定环境；第三方安装器遥测不等于已发生私有源码外传。 |
| 6：视频测量 | `measure.py` 限制本地常规文件、有限时间参数、输入协议和容器格式，FFmpeg 解码设 60 秒超时。 | 不替代媒体解码器本身的安全更新和资源隔离。 |
| 7：评审隐私 | A/B 答案不再默认保存绝对路径、SHA-256 或派生种子；重复 basename 通过输入索引区分；图像工具要求素材授权。 | 参考图像素和 basename 仍可能敏感，分享前需检查，答案文件仍应私有保存。 |
| 8：资源预算 | 限制边长、超采样像素、总帧数与 PNG 字节数；编码有超时；新增独立父进程监督 30 分钟墙钟时间，页面执行卡住时也会取消任务，随后有界清理子进程。 | 仍需 OS 级 CPU、内存、GPU 和磁盘配额。输出检查并非整个浏览器的硬磁盘配额。 |

### 本轮已执行的验收

- macOS、Python 3.12.13、Playwright 1.65.2、Chrome 158.0.8127.88 下，`PAPERCRAFT_BROWSER_TESTS=1 python -m unittest discover -s skills/papercraft/tests -v` 的 19 项检查通过。覆盖 HTTP/路径/日志边界、视频输入、A/B 脱敏、纹理写入失败保护、真实浏览器网络与 Service Worker 约束、重复视频输出、无 FFmpeg 的 PNG 回退，以及页面停滞后的浏览器清理和编码类子进程清理。新增诊断保留验收详见下节。
- 本地两个 Three.js 模块和许可文件的 SHA-256 与清单匹配；与 npm `three@0.187.0` 包逐字节一致，包的 SHA-512 与注册表完整性字段一致。
- 2D、3D、结构演示均在禁止实际外部页面请求的条件下启动。用基线提交的原始 HTML、同一浏览器和固定时钟重建基线，将其 CDN 请求以已校验的相同 npm 字节在本地响应；三个 640×360 画面与加固版本逐像素一致。此项不是对历史 CDN 实际响应或其他 GPU 后端的证明。
- 实际走通横屏、1080×1920 竖屏视频、3840×2160 且 `--ss 2` 的截图、命名 `spot`、3D 路线视频和步行镜头、完整视频测量 CLI、256 像素纹理及预览导出。视频测量可产生美术指标 WARN，不等于执行失败或所有视觉门槛通过。

### 诊断保留补强

- **从直接丢弃改为摘要与明细分离。** 标准输出仍仅展示每类前 40 条警告/异常样本，每条通常 400 字符，但显式说明截断和省略数量，并输出本次 `diagnostics-*.jsonl` 路径。全部控制台级别、页面异常堆栈及场景诊断在独立日志预算内保存，记录当前镜头和控制台来源位置；详细文件不会自动灌入代理上下文。
- **调试细节不再受摘要额度限制。** 构建指标不再只保留摘要的前 2400 字符，穿插诊断也不再只留前 12 项。两者在传出页面前按 65536 字符上限截取并标记，摘要仍保持短小；步行、路线和其他镜头诊断也写入明细。
- **保留安全和资源边界。** 每次日志至多 8 MiB，每个文本字段至多 65536 字符，来源 URL 至多 4096 字符；日志使用 JSON 转义和私有文件权限。容量不足时停止追加明细并累计省略数量，末尾写入保存、截断和省略计数。异常或可清理的取消记录追踪信息；强制终止后若没有末尾汇总，必须视为日志未完成。不能把“有明细文件”理解为任何日志洪泛下都无损。
- **已通过的验收：** 超过摘要数量的第 50 条消息、长消息尾部及控制字符可从 JSONL 还原，文件仅所有者可读写；有限容量下文件未超预算，截断和省略计数正确；Python 异常保留堆栈并标记中断。
- **已通过的真实浏览器验收：** 页面输出 50 条警告和一条仅入文件的 info，并抛出长异常；摘要保持限长、截图任务非零退出，但明细仍包含后续消息、异常尾部与函数栈、超过 2400 字符的构建指标尾部及全部 15 条穿插记录。页面停滞被监督进程取消时，诊断文件也完成中断汇总。

### 验收范围与剩余边界

按本次修复范围，Windows（包括 ANGLE 和进程树终止）不纳入实施与验收，不再作为待办或完成条件；保留现有兼容实现。这一范围约定取代原始计划中的 Windows 实测要求。

备用 Playwright Chromium 尚未实测；未验证实际容器/账户隔离、环境级出站拒绝及 CPU/GPU/内存配额，也未执行不受信任的真实攻击素材。相同输入在其他浏览器和 GPU 上的视觉一致性仍需单独验收。
