# 给你的多Agent管线装三道闸门——代码可以直接跑

## 先给你看一个让我血压飙升的场景

我写了一套 6 Agent 的代码审查管线：

```
代码扫描 → 安全审计 → SQL审查 → 配置检查 → 依赖分析 → 汇总报告
```

跑完，输出：「安全审计通过，SQL 审查通过，配置检查通过，依赖无异常。」

我差点就信了。

打开磁盘一看——第 2、3、4 步的 Agent 根本没产出文件。汇总 Agent 基于空气生成了一份报告。每一步都写着「通过」，每一步都没有真的执行。

而且从头到尾没报错。

这就是多 Agent 管线最阴险的失败模式——不是崩溃，是**静默错误**。Agent 输出了一段看起来特别靠谱的文字，但你没法知道它是不是真的干了活。

我当时就想：得有一道门禁。Agent 的产出不经过验证，不准传给下一个。

这篇文章就是把这个门禁的实现写清楚。代码可以直接跑。

---

## 三道闸门，一道比一道狠

逻辑特别简单。每个 Agent 产出之后，自动做三件事：

1. 你说的文件，真的存在吗？
2. 你说的验证，真的跑了吗？
3. 你写的文档，有没有泄底？

全过了，放行。任何一道不过——打回去，重做。

---

## 第一道：你说的文件呢？

Agent 特别喜欢说「我已经写好了 X」。

```python
import os
import py_compile

def check_file(filepath):
    """Agent 说写了这个文件——查一下是不是真的"""

    if not os.path.exists(filepath):
        return False, f"文件根本不存在: {filepath}"

    if os.path.getsize(filepath) == 0:
        return False, f"文件是空的: {filepath}"

    if filepath.endswith('.py'):
        try:
            py_compile.compile(filepath, doraise=True)
        except py_compile.PyCompileError as e:
            return False, f"语法都错了: {e}"

    return True, "ok"
```

几行代码的事。但关键是——这个检查是框架在 Agent 外面执行的。Agent 改不了 `os.path.exists()` 的返回值。它可以说「文件存在」，但文件系统不会骗人。

---

## 第二道：你真的跑了吗？

Agent 还会说「测试全过了」。

```
我运行了 pytest，结果如下：
tests/test_api.py::test_health PASSED
tests/test_api.py::test_create_user PASSED
=================== 3 passed ===================
```

你手动跑一下——5 个报错。Agent 编的。它根本没执行任何东西，就是在文本里写了几行看起来像测试输出的内容。

怎么防？让真实 Bash 执行来盖章：

```python
import subprocess
import re

def run_and_fingerprint(command):
    """跑命令，提取退出码指纹"""
    full_cmd = f"{command}; echo 'EXIT:'$?"

    result = subprocess.run(full_cmd, shell=True,
                            capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr

    match = re.search(r'EXIT:(\d+)', output)

    return {
        "output": output,
        "exit_code": int(match.group(1)) if match else None,
        "has_fingerprint": match is not None,
        "ok": match is not None and match.group(1) == "0"
    }
```

重点：`echo 'EXIT:'$?` 这行是拼在命令后面的，Agent 不知道你要拼接什么。它可以在自己的输出文本里写一百个 `EXIT:0`，但那是在它回复的文字里——不是 `subprocess.run()` 的实际输出。闸门看的是后者。

说人话就是：**Agent 可以在对话里吹牛，但命令行的退出码它控制不了。**

---

## 第三道：别把内部术语写到客户脸上

这个比较小但很实际。Agent 写文档的时候经常把内部术语带进去——「经过 quality_gate 验证，pipeline 状态为 PASS，CTO 审查评分 8/10」。

客户看到：？

```python
FORBIDDEN = [
    "管线", "pipeline", "R1", "R2", "R3", "R4", "R5", "R6",
    "Bash验证", "CTO审查", "quality_gate", "闸门判定",
    "Part A", "Part B", "审计报告"
]

def check_leak(text):
    leaked = [w for w in FORBIDDEN if w.lower() in text.lower()]
    return len(leaked) == 0, leaked
```

就这么多。三道闸门，代码加起来可能还没有一个 Agent 的提示词长。

---

## 装在一起

```python
class Gate:
    def __init__(self, agent_name, config):
        self.name = agent_name
        self.files = config.get("output_files", [])
        self.commands = config.get("verify_commands", [])
        self.public = config.get("customer_facing", False)

    def check(self):
        # 第一道
        for f in self.files:
            ok, err = check_file(f)
            if not ok:
                return "FAIL", f"文件有问题: {err}", self.name

        # 第二道
        for cmd in self.commands:
            r = run_and_fingerprint(cmd)
            if not r["has_fingerprint"]:
                return "FAIL", f"没找到 EXIT 指纹——Agent 可能伪造了验证结果", self.name
            if not r["ok"]:
                return "FAIL", f"命令退出码是 {r['exit_code']}", self.name

        # 第三道
        if self.public:
            for f in self.files:
                if f.endswith(('.md', '.txt')):
                    with open(f) as fh:
                        ok, leaked = check_leak(fh.read())
                    if not ok:
                        return "FAIL", f"文档泄漏内部术语: {leaked}", self.name

        return "PASS", None, None
```

---

## Agent 注册的时候声明验证规则

```python
agent_config = {
    "role": "R4_backend",
    "output_files": [
        "backend/main.py",
        "backend/models.py",
        "backend/requirements.txt"
    ],
    "verify_commands": [
        "ls -la backend/main.py && [ -s backend/main.py ]",
        "python -m py_compile backend/main.py",
        "python -m py_compile backend/models.py",
        "python -c 'from backend.main import app; print(\"ok\")'",
    ],
    "customer_facing": False
}

gate = Gate("R4_backend", agent_config)
status, reason, loopback = gate.check()

if status == "PASS":
    print("过，传给下游")
else:
    print(f"拦住了: {reason}")
    print(f"打回到: {loopback}")
```

---

## 跑起来长这样

正常情况：
```
$ python gate_demo.py
backend/main.py → 存在，非空，语法ok
backend/models.py → 存在，非空，语法ok
ls + py_compile → EXIT:0
import check → EXIT:0
过，传给下游
```

Agent 没产出文件的情况：
```
backend/main.py → 文件根本不存在: backend/main.py
拦住了: 文件有问题
打回到: R4_backend
→ Agent 收到: "backend/main.py 不存在，重新生成"
```

Agent 编造测试结果的情况：
```
pytest → 没找到 EXIT 指纹——Agent 可能伪造了验证结果
拦住了
→ Agent 收到: "验证命令没有 EXIT 指纹，重新跑"
```

---

## 失败之后呢——定向回环

一般框架失败处理就是「重试」——整个流程重新跑一遍。太蠢了。

AgentGate 是按错误类型精确回退：

```python
def where_to_go(fail_reason, current_agent):
    if "文件" in fail_reason or "语法" in fail_reason:
        return current_agent        # 代码问题 → 回开发自己
    if "架构" in fail_reason:
        return "R2_designer"         # 架构问题 → 回设计层
    if "安全" in fail_reason:
        return "R6_security"         # 安全问题 → 回安全审计
    return "HUMAN"                   # 判断不了 → 叫人
```

不该回环的不乱回，该找谁找谁。

---

这套东西的核心思路就一个：**别信 Agent 说的话。信命令行返回的结果。**

76 个测试全过，MIT 协议，代码在 GitHub：

[github.com/zw-onemaker-ai/agent_gate](https://github.com/zw-onemaker-ai/agent_gate)

下一篇写「弱模型也能跑生产——Ollama + Qwen 上跑 AgentGate」，有兴趣可以 Watch。
