# 项目演进

## 初始意图

以下根据现存 SPARK.md 的起源或背景章节保留；未通过早期历史独立核实。

loom 最早是一个 filesystem-first 的 Python CLI：人类把需求丢进 `.loom/inbox/`，agent 从 `.loom/threads/` 里捡下一步动作，双方共同推进同一份 markdown 状态机。它解决了"一个仓库里多个 agent 怎么不打架"的问题，但越往下走越暴露同一类裂缝：

- markdown 既是真相又是 UI，状态在文件锁、序号、frontmatter 之间漂移；
- routine / inbox / mailbox / hook 各有一套语义，互相不正交；
- agent 调用、进程执行、项目动作全部沉到同一个 CLI 里，边界含糊；
- 想恢复一次"昨天跑到一半的事情"，没有可信的事件日志。

与此同时，automation-repo.md 里沉淀出一套更克制的设计判断 —— Durable Agent Workflow Kernel：只做 workflow 定义、agent orchestration、版本/运行/暂停/历史管理；trigger、agent、workspace 全部走端口。这套思路与正在成形的 4-repo 闭环（loom / weft / warp / cue-shell）天然契合。

新一版 loom 由此被定为**船新设计**：保留"workflow 是一等公民"的原始直觉，把 markdown 状态机降级为投影，把 agent 和 trigger 推到端口外，用 Rust 重写一个可恢复、可审计、可版本化的 durable runtime。

## 2026-10-03

### 触发

用户要求将 SPARK.md 项目记录迁移到 zendev evolution 格式。

### 变化

现行项目演进记录改用 EVOLUTION.md；原 SPARK.md 保留为待复核来源。以下完整保留源文件内容（仅降低标题级别），包括 frontmatter、历史、决策和未完成目标。原文内 SPARK 引用属于历史材料。

```yaml
---
description: loom 是 4-repo 闭环里的 durable workflow runtime / automation kernel，用 Rust 重写，事件日志为真，agent 与 trigger 都是端口。
owner: zrr1999
created: 2026-04-26
updated: 2026-04-26
inspired_by:
  - loom
  - automation-repo.md
  - weft
  - warp
  - cue-shell
  - Durable Agent Workflow Kernel
  - workflow-defined automation
---
```

##### 起源

loom 最早是一个 filesystem-first 的 Python CLI：人类把需求丢进 `.loom/inbox/`，agent 从 `.loom/threads/` 里捡下一步动作，双方共同推进同一份 markdown 状态机。它解决了"一个仓库里多个 agent 怎么不打架"的问题，但越往下走越暴露同一类裂缝：

- markdown 既是真相又是 UI，状态在文件锁、序号、frontmatter 之间漂移；
- routine / inbox / mailbox / hook 各有一套语义，互相不正交；
- agent 调用、进程执行、项目动作全部沉到同一个 CLI 里，边界含糊；
- 想恢复一次"昨天跑到一半的事情"，没有可信的事件日志。

与此同时，automation-repo.md 里沉淀出一套更克制的设计判断 —— Durable Agent Workflow Kernel：只做 workflow 定义、agent orchestration、版本/运行/暂停/历史管理；trigger、agent、workspace 全部走端口。这套思路与正在成形的 4-repo 闭环（loom / weft / warp / cue-shell）天然契合。

新一版 loom 由此被定为**船新设计**：保留"workflow 是一等公民"的原始直觉，把 markdown 状态机降级为投影，把 agent 和 trigger 推到端口外，用 Rust 重写一个可恢复、可审计、可版本化的 durable runtime。

##### 产品设计目标

loom 是闭环系统的 **workflow 编排核心**。它的最终形态应该让用户感觉：写一份 workflow，就是在描述"一件值得反复发生的事情"，而不是在写脚本、写 cron、写 agent prompt 的胶水。

具体期望的形态：

- **一切围绕 Source Package → Release Package → Run Instance 三层抽象**。Source 可编辑，Release 内容寻址不可变，Run 永远绑定某个固定版本 hash。"配置没变但 prompt 偷偷变了"这种假一致从结构上不可能发生。
- **event log 是真相，snapshot 是缓存，markdown 是投影**。任何一次 run 在进程崩溃后都能从事件日志重建；当前 `.loom/threads/` / routines / `start.sh` zellij 编排都退化为 legacy view 或 adapter，不再承担正确性。
- **节点词汇收敛到 agent / router / suspend / subflow / map / end**；数据命名空间收敛到 `$input / $state / $meta / $output`。Suspension 把 approval、human input、external callback 统一成一种东西。
- **trigger v1 只做两件事**：manual prompt 与 interval schedule。webhook / fswatch / cron 表达式留到端口外，由外部系统通过 TriggerPort 推进 run。原先的 routines 自动迁移为 workflow 的 interval trigger，不再保留独立的 routine 概念。
- **agent 调用全部交给 weft**：loom 不 fork/exec、不直接对接 Copilot CLI / Codex / OpenCode、不知道 PTY。AgentPort 只关心"给一个节点契约，拿回一个结构化结果"。
- **进程执行交给 cue-shell，项目动作交给 warp**：loom 不写项目 recipe，不直接 `Command::spawn`；要跑命令就走 cue-shell，要做"在这个项目里发布一个版本"这种动作就调 warp。

##### 目标用户

- **owner 自己**：在多个仓库之间跑长生命周期的自动化（review、release、定时巡检、跨仓重构提案），需要"昨天跑到一半的事情今天能接上"。
- **少数有耐心写 workflow 的开发者**：愿意把"我希望这件事每天都发生 / 在某个事件后发生 / 在人审核后继续"这件事写成 TOML + prompt 包，而不是写一坨 shell。
- **闭环里的其他 repo（weft / warp / cue-shell）**：作为下游消费者，通过端口把 loom 当作 durable orchestrator 使用。

显式不为以下人群设计：想要"低代码可视化自动化平台"的用户、想要多租户 SaaS workflow 服务的团队、想要 loom 直接管 agent prompt 工程的人。

##### 核心原则

- **Workflow 是一等公民，markdown 不是**。状态机的真相在事件日志里；任何 markdown 视图都是可重建的投影。
- **内容寻址即版本身份**。`workflow.toml`、prompt、instruction pack、schema、subflow 一起进入 hash；release package 一旦发布即不可变。
- **Kernel 只做编排，不做厂商集成**。AgentPort / TriggerPort / StorePort / WorkspacePort 是硬边界；任何"为某个 agent / 某个 trigger 特别开洞"的诱惑都要拒绝。
- **可恢复优先于性能**。每一次状态变化先落事件日志再继续；snapshot 只是加速器，丢了不影响正确性。
- **外层编排由 kernel 控制，内层思考由 agent 控制**。一次 agent 执行对 kernel 永远是"带输入/输出契约的黑盒节点"。
- **悬挂统一**。approval、human input、external callback 都是同一种 suspension，由外部通过 resume token 推进。
- **Rust，且小**。runtime 用 Rust 重写，依赖收敛，构建产物单一可分发；Python 旧实现进入 legacy 区。

##### 能力地图（方向性）

- **Workflow Compiler**：解析 TOML、加载外部 prompt / pack / schema / subflow，做静态校验，输出 canonical manifest 与 `workflow.lock.toml`，计算 version hash。
- **Workflow Registry**：存储 release package，管理 `workflow_id → active_version_hash`，支持回滚 / 激活 / 历史查询。
- **Run Engine**：基于 release package 创建 run，推进状态机，执行节点，记录事件，保存 snapshot，处理中断与恢复。
- **Run Store（v1）**：SQLite 存 event log + run metadata + suspension records；快照与大对象走 content-addressed blob store。
- **AgentPort**：以契约方式调用 weft，拿回结构化输出；可选 resume / cancel。loom 自身不知道任何具体 agent 实现。
- **TriggerPort（v1）**：manual prompt + interval schedule 两类内置 trigger；其余 trigger 留作端口外实现。
- **Suspension 模型**：统一处理 approval / human input / external callback，提供 resume token 与超时策略。
- **Subflow / Map**：支持子流程嵌套与批量 fan-out，作为外层编排原语，而不是 agent prompt 的语法糖。
- **Projection / Adapter 层**：把现有 `.loom/threads/`、routines、`start.sh` zellij 编排作为只读投影或迁移适配器暴露，保证旧使用习惯有平滑过渡路径。
- **CLI / 服务双形态**：CLI 用于本地交互与一次性运行；长驻 runtime 用于持续推进 run、处理 trigger 与 suspension。<!-- 待确认：v1 是否需要长驻进程，还是先做 oneshot tick -->

##### 成功信号

- 一次跑了一半的 run，进程被 kill 后再启动 loom，能从最后一个事件继续推进，没有重复副作用。
- 同一个 workflow 名字在两个时间点跑出来的两个 run，可以精确说出"它们用的是哪个版本 hash、差在哪一个 prompt"。
- 把一个原本写在 routine 里的"每 6 小时巡检"迁移到 workflow trigger，用户感觉只是换了写法，没有功能损失。
- 用户写一个新的 workflow 时，不需要在 loom 仓库里碰任何 agent / 进程 / 项目动作的代码 —— 只调 weft / cue-shell / warp 暴露的端口。
- 想"加一种新的 trigger"时，开发者写在 loom 之外，通过 TriggerPort 接入，不需要改 kernel。
- 删掉 `.loom/threads/` 整个目录后，run 仍然能正确推进；markdown 视图重新生成后内容与 event log 一致。

##### 生态关系

loom 处在 4-repo 闭环的中心：

- **prompt / schedule → loom**：人类（或上游系统）通过 manual prompt 或 interval schedule 触发 workflow run。
- **loom → weft**：所有 agent 调用走 AgentPort，由 weft 这个 agent runtime / control plane 落地具体的 Copilot CLI / Codex / OpenCode / superagent。loom 不知道任何具体 agent 实现细节。
- **loom → cue-shell**：所有进程执行（跑命令、跑脚本、跑构建）通过 cue-shell 这个 bash-like durable process substrate。loom 不 fork/exec、不管 PTY、不管输出流。
- **loom → cue-shell → warp**：项目级动作（"在这个仓库里跑发布"、"在那个仓库里跑一次 lint 全套"）的 recipe 写在 warp 这个项目执行基础层 CLI 里。loom 通过 cue-shell 启动 warp 命令，而不是直接调 warp 库。
- **依赖方向只能单向**：loom 调下游（weft / cue-shell / warp），但 loom 不被它们反向依赖。weft 不直接知道 loom；warp 不直接知道 loom。这样闭环里任何一端都可以独立替换。

##### 什么不是本项目要做的（Non-goals）

- 不做通用分布式任务调度平台或多租户 control plane。
- 不做 cron 表达式、webhook 网关、fswatch、Git hook 等任意一种新 trigger 的内置实现（除 manual + interval 之外）。
- 不做 agent 内部工具编排、prompt 工程、模型路由 —— 全部下沉到 weft。
- 不做项目级 recipe（构建、发布、测试编排）—— 全部下沉到 warp。
- 不做 PTY、shell、进程树管理 —— 全部下沉到 cue-shell。
- 不做 UI / dashboard / 实时可视化；任何前端都是 event log 的下游消费者。
- 不再保留独立的 "routine" / "agent CLI 直连" 概念：routines 迁移为 workflow trigger，agent 操作迁移到 weft。
- 不为某个特定厂商 agent 在 kernel 里开特例分支。
- 不承担 secret 注入、sandbox、policy enforcement、产物投递、外部通知 —— 这些都是端口外能力。

##### 已考虑的替代方案 & 理由

- **继续演进现有 Python loom**：可以更快上线，但 markdown-as-truth 的根性问题不解。"事件日志为真"几乎要重写存储层、调度层和 CLI 层；与其在原结构里改，不如借迁移机会换 Rust。
- **借 Temporal / Cadence 等成熟 workflow engine**：能力远超需求，且与 4-repo 闭环的"小、可分发、单二进制"目标冲突；同时它们对 agent / suspension / human-in-the-loop 的语义不匹配，硬塞会让 kernel 变厚。
- **直接借 WARP 风格的 A0 primary operator + control plane**：A0 是"agent team runtime"，把策略与执行混在 agent 内部，与本项目"kernel 只做外层编排"的边界感冲突。WARP 的 checkpoint / durable handoff 思路被吸收，但不照搬其架构。
- **借 Symphony 整套分层（policy / configuration / coordination / execution / integration / observability）**：分层思想很好，但 Symphony 的组件粒度对单人/小团队过重；本项目只在"orchestrator 是唯一修改调度状态的组件"这一条上对齐，其余按需取用。
- **借 Hermes 的 long-running single agent + skills + cron**：Hermes 的 cron 任务本质是 fresh self-contained sessions，不是 workflow-state-first 的 durable orchestrator；reusable instruction packaging 思路被借入 release package，但不照搬其 runtime 形态。
- **routine 保留为独立概念**：增加了一种和 workflow 重复的状态机，长期会和 trigger 形成两套调度。决定折叠为 workflow 的 interval trigger。
- **存储用 Postgres / sled / 自研 LSM**：v1 单用户单机场景下 SQLite 足够；event log + snapshot + content-addressed blob 三件套在 SQLite 上能走得很远，迁移成本可控。

##### 开放问题

- v1 是否需要一个长驻 runtime 进程？还是先做"每次 CLI 调用 tick 一下 run engine"的 oneshot 模型？<!-- 待确认 -->
- AgentPort 与 weft 之间走什么协议（本地 IPC / HTTP / 子进程 stdio）？<!-- 待确认 -->
- Release package 的物理形态：单文件 tar+hash，还是 SQLite 内表？<!-- 待确认 -->
- 旧 `.loom/threads/` / routines / mailbox 的迁移策略：一次性导入 event log，还是只读 projection 共存一段时间？<!-- 待确认 -->
- Suspension 的超时与"过期 run"语义：是直接失败、自动重试，还是进入人工队列？<!-- 待确认 -->
- 多仓库场景下，loom 自身的运行位置：每个仓库一个 `.loom/`，还是用户级单点 `~/.loom/` 收口？<!-- 待确认 -->
- workflow.toml 的字段集与 schema 演进策略：lockfile 怎么处理 schema 升级？<!-- 待确认 -->

##### 修订记录

- 2026-04-26：初稿。确立 Rust 重写、SQLite event log、manual + interval trigger、routines 折叠为 workflow trigger、agent 全面下沉到 weft 的设计共识，并把 automation-repo.md 的 Durable Agent Workflow Kernel 并入 loom。


### 理由

采用 zendev 的可校验演进格式，同时避免在缺乏逐日证据时虚构历史触发或理由。后续方向变化按真实日期升序追加唯一的 `## YYYY-MM-DD`，同日变化合并，并依次填写 `### 触发`、`### 变化`、`### 理由`。编辑后运行 `zendev evolution check`（或独立组件 `zendev-evolution check`）；更正已有文字时审阅 Git diff。格式依据 [zendev evolution 指南](https://github.com/zendev-lab/zendev/blob/main/docs/guides/evolution.md)。
