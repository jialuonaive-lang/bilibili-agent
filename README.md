# B站兴趣视频筛选与主动推送 Agent

课程项目：用户提供兴趣，OpenAI/Codex通过原生动态工具自主决定检索、读字幕、缺字幕转写和推荐提交。程序负责验证参数、内容依据与唯一收件人，并保存真实Action/Observation日志。定时入口能按限定轮数周期运行。

## 本项目包含什么

- 独立业务工具循环，使用官方Codex app-server `dynamicTools` / `item/tool/call`，不是预填假的工具调用日志。
- 五个业务工具：`get_preferences`、`search_videos`、`read_video`、`transcribe_audio`、`send_recommendations`。
- 按BV号去重；只允许推荐取得字幕或转写的候选；逐字核对证据引文。最多两条，消息不超过480字。
- 发送前再校验完整消息中的链接：标题或理由夹带未核实链接（含YouTube）时拒绝发送，不能仅靠系统提示保证来源。
- 默认预览，`--live`才执行私信。唯一目标由本机配置给出，模型没有修改收件人的参数。无公开评论、点赞或群聊工具。
- 对重复、参数错误、无内容、转写时长、超时、未知回执和写盘故障进行专属测试。

不包含原工程中的群聊角色、群友记录、故事系统、桌面遥控或任何账号凭据。视频分析以字幕/音频文字为依据，不宣称看过完整画面。

## 环境与安装

本次实测Windows、Python3.14.8、Codex CLI 0.163.0-alpha.11.1。可运行Python3.11+；非实测平台需自行验证。动态工具是实验接口，其他Codex版本可能需要适配。Windows优先使用桌面内置原生Codex，也可通过`CODEX_EXECUTABLE`指定路径。仅安装旧npm Codex0.92不能复现本项目协议。

```text
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
codex login
```

模型沿用本次账号可用的`gpt-5.6-sol`、`xhigh`。复现者应使用自己的合法登录；账号可用模型不同，可修改配置，但不能把不同模型的结果冒充本报告结果。

复制`config.example.json`为`config.local.json`，在本机填写：

1. 兴趣列表、模型名称。
2. `credential_path`：自己已登录B站账号的凭据JSON路径，字段按SDK的`sessdata`、`bili_jct`、`buvid3`、`dedeuserid`配置。凭据文件留在本机，不能上传仓库。
3. `sender_uid`和`recipient_uid`：自己的发送号与获准接收号。发送前会通过平台核对发送号身份。
4. 可选转写环境：`asr_python`、`asr_cache`、`asr_model`。转写依赖`faster-whisper`及预先下载的对应模型。示例默认small；低内存机器可选择base，质量与资源开销不同。

本项目复用登录和模型服务，仍需要网络；模型没有通用联网或电脑权限。B站平台可能要求验证、拒绝陌生人私信或改变接口，这类失败不能靠脚本保证消除。

## 运行

```text
python run.py --request "推荐一条AI编程实测视频，关注代码可靠性" --run-id demo-preview
python run.py --request "请分析BV号对应的视频，先核实字幕" --run-id another-preview
python run.py --request "推荐一条独立游戏开发日志" --live --run-id demo-live
python run.py --request "给我推荐点东西" --followup "独立游戏开发日志，三分钟以内，只预览一条" --run-id clarify-preview
python schedule.py --request "推荐独立游戏开发与AI技术视频" --rounds 2 --interval-minutes 60
python -m unittest discover -s tests -v
```

`--run-id`只允许字母、数字、短横线或下划线。已存在的证据目录不会覆盖。定时入口默认预览，最多24轮，每轮仍受16次工具执行与360秒超时约束；初次试运行建议只设1轮。没有部署Windows开机服务，也没有宣称验证过全天候无人值守。

`--followup`提供脚本化的用户补充输入，最多两次；各轮在同一个临时线程内执行，共享本轮工具预算、已读候选和唯一发送提交限制。它用于复现澄清分支，不是额外调用的新聊天账号。首次复现缺兴趣追问时，请将示例配置的`interests`设为空列表。

## 文件与证据

| 文件 | 用途 |
|---|---|
| `app/codex_rpc.py` | 原生协议连接、注册工具、回传结果；不读取/打印登录令牌 |
| `app/tools.py` | 工具JSON Schema、宿主校验、日志、发送状态 |
| `app/bilibili_adapter.py` | B站SDK检索、字幕、音频与私信 |
| `app/asr_worker.py` | 可选隔离转写进程，CPU int8 |
| `prompts/system.txt` | 本次Agent的核心系统提示 |
| `run.py` / `schedule.py` | 单轮与有限定时入口 |
| `tests/` | 专属隔离测试，平台副作用均为mock |
| `evidence/` | 实测动作、观测、最终结果、时间与模型usage |
| `data/recommendation-state.json` | 本机已推荐BV与发送尝试状态，不公开提交 |

原始证据的`kind=action`来自真实模型工具请求；`observation`来自工具执行结果。`purpose`是模型提供的公开操作目的，不是私有思维链。报告中的流程归纳会明确标注为作者整理，不能冒称模型原始Thought。

`happy-preview`与`complex-asr-live`使用真实B站数据。后者历史目录名含asr，但实际执行的是字幕筛选和真实私信，报告按实际轨迹解释。`actual-asr-preview`是一次真实转写失败的降级。`missing-preferences`、`injection`、`timeout`为真实模型加明确合成SDK资料/故障注入；这些不冒称平台端故障。专属单元测试均不发送真实消息。

平台成功回执只证明接口受理，不证明用户已经阅读。缺回执时保存uncertain且不自动重发；为防刷屏，允许少发，不保证每条都能自动补发。字幕来自视频作者/平台，内容中的统计数字不等于本系统已独立验证。

当前交付的是文字消息中的B站视频链接和推荐理由，未实现原生封面视频卡片。已查证当前Web私信接口只支持文字、图片、撤回；视频卡片尝试返回参数错误，旧分享路径返回404，失败原型没有部署。不要把普通超链接描述为已完成原生卡片功能。

转写验收状态：small/base最初曾因资源或MKL初始化失败，之后关闭Packed GEMM并修正Windows WMI阻塞；早期素材返回空语音文本。后续`bili-request-7699738169690852827`对449秒的无字幕视频BV1Mbn26aE5S实际转写得到3815字，非缓存命中，转写耗时133.388秒；完整推荐轮172.481秒、6次工具调用，真实私信已回读核验。这是对用户既有请求的受控补测，不是伪造的新入站事件，也不代表语音识别准确率已被人工逐字验收。`actual-asr-final-preview`仍保留为0工具的模型线程启动失败，不能改写为成功。

`clarification-preview`为真实模型和真实平台数据的两轮案例：测试脚本先提出模糊请求，再补充明确兴趣。在同一线程内先追问、再检索核实，69.019秒、14次业务调用，最终只生成一条预览。用户输入是预先编写的测试情境，不冒称现场人工对话；日志中的`purpose`为模型原样提供的公开操作目的。

## 来源与自主改造

前期需求参考了用户提供的社区项目`https://github.com/c1overzz/dsh-bili-scroll`，本次提交未复制该项目整个插件或聊天记录。业务数据依赖`bilibili-api-python`，转写依赖`faster-whisper`/CTranslate2，模型连接依据OpenAI官方app-server协议。自主改造包括原生动态工具宿主、逐字证据校验、有界执行、发送状态管理、专属异常测试和真实案例日志。开发使用AI辅助，测试结果与来源分级由作者核验。

官方协议：https://learn.chatgpt.com/docs/app-server

## 提交检查

报告必须导出PDF并补齐学号/班级，按`学号_姓名_项目名.pdf`命名。提交包只包含源码、示例配置、依赖、提示词、测试和脱敏证据；不得提交`config.local.json`、凭据文件、音频缓存、完整模型权重或旧群聊资料。公共仓库地址需要填写实际可访问的地址，不能用占位地址假称已发布。
