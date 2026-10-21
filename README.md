# Codex 按模型区分的 AGENTS 规则（GPT-6-Sol / GPT-6.1-Sol / GPT-6-Astra）

Codex 只认一个全局 `~/.codex/AGENTS.md`，不区分模型。这套配置用官方的 SessionStart hook 补上这一层：每次新建对话（以及 `/clear`、上下文压缩之后），hook 读取当前对话的模型名，把对应规则作为 developer 上下文注入。其他模型不注入任何内容。

## 解决什么问题

- **GPT-6.1-Sol / Astra “说什么做什么”**：加入“多想一步”的范围阶梯——真实目的必需的改动直接做，任务外发现的问题只指出，重构和新功能只建议；歧义时先做一版合理默认实现；不提前收工；统一收尾汇报格式。
- **过度验证**（冒烟测试、SHA256 核对、重复跑测试）：只做最能暴露问题的那一项检查，点名禁止仪式性验证。
- **各模型的已知短板**：Astra 易提前停、爱提问；6.1-Sol 谎报完成和被拦后绕行比例偏高（见 GPT-6.1 Sol 系统卡）；6-Sol 内置提示词较旧。

小规模 A/B 测试（同一任务各跑一次，low 档）：6.1-Sol 从“只改点名的函数、实际导入仍失败”变为改通整条导入链路；复制文件任务中 Astra 不再做 SHA256 比对；仪式性检查（`git diff --check` 等）消失，耗时缩短 20%–43%。样本量小，仅供参考。

## 文件

| 文件 | 放到 |
| --- | --- |
| `AGENTS.md` | `~/.codex/AGENTS.md`（所有模型共用，**先改“用户与环境”一节**） |
| `model-agents/AGENTS.gpt-6-astra.md` 等三个规则文件 | `~/.codex/model-agents/` |
| `model-agents/session_start.py` | `~/.codex/model-agents/` |
| `hooks.json` | `~/.codex/hooks.json` |

如果你已有 `~/.codex/AGENTS.md` 或 `hooks.json`，先备份，再合并而不是直接覆盖。

## 安装

1. 按上表复制文件（`~` 在 Windows 上是 `C:\Users\你的用户名`）。
2. 编辑 `hooks.json`：
   - Windows：把 `commandWindows` 改成实际路径，例如 `C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe -I C:\Users\you\.codex\model-agents\session_start.py`（路径含空格时用不含空格的路径或加引号）。用 `where python` 查 Python 路径。
   - macOS / Linux：`command` 保持 `python3 ~/.codex/model-agents/session_start.py` 即可。
3. 重启 Codex，然后信任 hook（Codex 要求本人审核非托管 hook）：
   - Codex App：输入框旁带黄点的 **Review hooks** → 信任。
   - CLI：运行 `codex`，输入 `/hooks`。
4. 新建一个 Astra 或 6.1-Sol 对话即生效。修改规则文件不需要重新信任；修改 `hooks.json` 里的命令需要。

## 说明

- 规则在对话开始时注入；对话中途换模型，已注入的规则不会跟着换。
- 单个规则文件建议控制在 2500 token 内（`additionalContextLimit` 设为 4000）。
- 不想用了：删除 `~/.codex/hooks.json` 即可，全局 AGENTS.md 照常生效。
- 规则依据：OpenAI《Using GPT-6》指南、“Rethinking skills and prompts for GPT-6 Astra”博客、GPT-6.1 Sol 系统卡，以及社区反馈（r/codex、V2EX、HN、openai/codex issues）。
