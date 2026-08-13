# 即刻（3条）

---

一个有点反常识的事：多 Agent 管线翻车，跟模型强不强关系不大。

我跑了 100 多次 12 Agent 的管线，最常见的翻车是——Agent 说「我做好了」但你一看，文件根本没生成。Claude 这么干，Qwen 也这么干。不是智力问题，是没人在验证。

后来我搞了一套三道闸门：文件存在检查 + Bash 退出码指纹 + 文档脱敏。Agent 产出必须过闸门才能传给下游。代码开源了。

Agent管编，闸门管信。
github.com/zw-onemaker-ai/agent_gate

---

推荐个自己做的工具。解决一个问题：你让多个 AI Agent 协作的时候，怎么保证它们不互相传垃圾？

做法很直接——三道闸门，每道都绕不过去：
1. 你说的文件，ls 一下看真存在吗
2. 你说的测试全过，Bash 退出码贴出来（Agent 伪造不了）
3. 你写的文档，别把内部术语露给客户

76 测试，支持 Ollama/百炼/DeepSeek，MIT，随便用。

github.com/zw-onemaker-ai/agent_gate

---

LangChain、CrewAI、AutoGen、Dify——都是好东西。但它们全都默认一个事：Agent 的产出是可信的。

这个假设是错的。

Agent 说写了文件但没写，说跑了测试但没跑，改了接口但不通知下游。这些不是偶发 bug，是系统性漏洞。因为没有一个框架在 Agent 之间放了一道验证。

AgentGate 就是干这个的。坐在所有 Agent 框架下面，管记忆、管上下文、管幻觉拦截、管提示词质量、管流程自愈。

Agent管编，闸门管信。
github.com/zw-onemaker-ai/agent_gate
