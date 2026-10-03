# 千问渠道与聊天模型切换

## 启用

1. 在千问AI平台创建 API Key，在本地 `.env` 中填写 `QIANWEN_API_KEY`。
2. 重启后端，刷新前端。设置中的“千问AI平台”显示密钥已配置。
3. 点击聊天输入框下方的模型按钮，选择 Qwen3.8 Flash、Qwen3.7 Plus、Qwen3.8 Max 或 Qwen3.7 Flash。

API 地址为 `https://maas.qianwenaiapi.com/compatible-mode/v1`。
原 Qwen 国际和国内 DashScope 渠道、DeepSeek 官方渠道保持独立。
`QIANWEN_API_KEY` 是本项目给千问平台密钥设置的环境变量名；它与其他渠道的密钥不自动互用，密钥不会发给前端。

## 选择的作用域

- 聊天选择以 `model_selection: {provider, model}` 随任务提交。无需修改全局默认渠道。
- 显式选择覆盖本次任务的默认与深度模型；专业角色和子工具继承相同任务配置。预算、权限、日期、证据和模拟盘审批保持原约束。
- “跟随默认模型”恢复设置中的全局策略，包括可选的深度模型。
- 已提交的任务使用配置快照；运行中切换按钮只影响后续提交。
- 浏览器只记住渠道和模型 ID，刷新后保留选择。密钥和 API 地址只由后端决定。
- 未配置密钥的千问模型禁用并提示环境变量；后端再次校验，拒绝该请求，不创建任务或降级成普通文字回答。
- `task_created` 与角色运行记录保留实际渠道和模型，历史任务可以回放。

## 思考与工具调用

设置中的千问深度思考默认关闭，可为新任务启用。思考 Token 计入输出费用。
适配器保留并回传 `reasoning_content`，支持原生多轮工具调用；结构化输出不会强制发送思考模式不支持的 `tool_choice`。

## 验证边界

本地使用拦截 HTTP 请求验证官方接口地址、独立密钥、思考开关、工具调用参数、工具结果回传和后续回答。
任务回归验证全局配置变化不影响在途任务及子工具、跨渠道不复用旧 API 地址、不继承另一渠道的深度模型、无密钥不留半成品任务。
浏览器验证输入框模型菜单、实际提交参数、刷新记忆、恢复默认及缺密钥提示。
未配置真实千问密钥时，不声称已通过真实模型调用或模型质量评测。

官方资料：
- https://platform.qianwenai.com/docs/developer-guides/getting-started/introduction
- https://platform.qianwenai.com/docs/developer-guides/tool-calling/function-calling
- https://platform.qianwenai.com/pricing/api
