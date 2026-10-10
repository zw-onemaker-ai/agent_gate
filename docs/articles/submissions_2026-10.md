# 投稿物料（2026-10-10 准备）

> 目标渠道：HelloGitHub（月刊收录）+ 阮一峰科技周刊（issue 直投）
> payload 已就绪：`publish_kit/payloads/`——用户确认后可一键提交

## 1. HelloGitHub 投稿

**标题**：`[开源推荐] AgentGate：给多 Agent 管线装可靠性闸门`

### 项目地址

https://github.com/zw-onemaker-ai/agent_gate

### 类别

Python

### 项目标题

给多 Agent 管线装可靠性闸门：产出验证+定向回环

### 项目描述

一个多 Agent 编排框架，默认不信任任何 Agent 产出：每步输出必须通过「文件存在、EXIT_CODE 指纹、脱敏检查」验证才能传给下游，失败按类型定向回环修复，连续失败升级人工闸门。实测让 12 步管线在本地小模型上也能稳定跑完。

### 亮点

- **执行脑是纯规则引擎**：调度/验证/回环判定不调大模型，每个 PASS/FAIL 可追查、可复现——连主脑都被闸门约束
- **弱模型也能跑生产**：不用最强模型，Ollama 本地模型 + Qwen 实测跑通完整管线
- **断点恢复**：管线崩在第 8 步，恢复后从第 8 步继续，不用从头重跑
- **零第三方依赖**：纯 Python，127 个测试 + GitHub Actions CI 全绿
- 附 3 个可运行 Demo，**不用模型不用 key**，clone 下来 1 分钟看到效果

### 示例代码

```bash
git clone https://github.com/zw-onemaker-ai/agent_gate
cd agent_gate
python3 examples/demo_minimal.py --mock   # 无需模型：看闸门和验证怎么工作
python3 -m pytest                          # 127 个测试
```

### 截图或演示视频

![AgentGate 五层可靠性架构](https://raw.githubusercontent.com/zw-onemaker-ai/agent_gate/master/docs/images/five_layers.svg)


## 2. 阮一峰周刊投稿

**标题**：`【开源自荐】AgentGate：给多 Agent 管线装可靠性闸门`

阮老师好，自荐一个开源项目：

https://github.com/zw-onemaker-ai/agent_gate

它是一个多 Agent 管线的「可靠性层」。起因是我用多个 AI Agent 协作跑 12 步开发管线，被「静默失败」坑了很多次：Agent 说「测试全过」，实际没跑；说「文件写好了」，磁盘上是空的。我统计了一下，不加验证的管线成功率只有六成左右，而且大部分失败没有任何报错。

AgentGate 的思路是把「不信任」做成机制：每个 Agent 的产出必须过三道闸门才算数——① 产出文件真实存在且非空；② 所有验证命令必须贴出 EXIT_CODE（Agent 可以编话，编不了退出码）；③ 面向用户的产出过脱敏检查。任何一道不过，按错误类型定向回环给对应 Agent 修，连续失败升级人工闸门。

几个我觉得有意思的点：执行调度是纯规则引擎，一行大模型都不调——PASS/FAIL 可追查、可复现；管线跑到一半崩了能断点恢复；127 个测试全过。仓库里有 3 个 Demo，clone 下来不用模型、不用 key，一条命令就能看到闸门怎么拦截假产出。

MIT 协议，欢迎提 issue 和拍砖。
