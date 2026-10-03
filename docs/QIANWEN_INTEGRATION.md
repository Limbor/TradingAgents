# 千问渠道与聊天模型切换

## 启用

1. 在千问AI平台创建 API Key，在本地 `.env` 中填写 `QIANWEN_API_KEY`。
2. 重启后端，刷新前端。设置中的“千问AI平台”显示密钥已配置。
3. 点击聊天输入框下方的模型按钮，选择 Qwen3.8 Flash、Qwen3.7 Plus、Qwen3.8 Max 或 Qwen3.7 Flash。

API 地址为 `https://maas.qianwenaiapi.com/compatible-mode/v1`。
原 Qwen 国际和国内 DashScope 渠道、DeepSeek 官方渠道保持独立。
`QIANWEN_API_KEY` 是本项目给千问平台密钥设置的环境变量名；它与其他渠道的密钥不自动互用，密钥不会发给前端。

## 选择的作用域

- 聊天输入框和设置页复用同一模型菜单，读取和写入后端的 `llm_provider` / `model_policy`。
- 普通切换将所有角色统一到所选模型，并清除旧的深度模型覆盖；换渠道时清除上一渠道的自定义服务地址。
- 高级模型设置支持自定义模型 ID、服务地址和可选的研究裁决模型覆盖。开启覆盖后，应以任务档案中的实际角色模型为准。
- 旧浏览器独立模型偏好不再生效；刷新或进入其他页面均读取服务端配置。
- 已提交任务使用配置快照，切换只影响新任务；保存模型期间不能提交消息。保存失败时保留原模型并提示错误。
- 未配置密钥的模型禁用并显示配置提示。密钥和 API 地址由后端管理。
- REST API 仍支持显式 `model_selection` 作为单任务覆盖，聊天界面不再使用此独立配置。
- `task_created` 与角色运行记录保留实际渠道和模型。

## 思考与工具调用

设置中的千问深度思考默认关闭，可为新任务启用。思考 Token 计入输出费用。
适配器保留并回传 `reasoning_content`，支持原生多轮工具调用；结构化输出不会强制发送思考模式不支持的 `tool_choice`。

## 验证边界

本地使用拦截 HTTP 请求验证官方接口地址、独立密钥、思考开关、工具调用参数、工具结果回传和后续回答。
任务回归验证全局配置变化不影响在途任务及子工具、跨渠道不复用旧 API 地址、不继承另一渠道的深度模型、无密钥不留半成品任务。
浏览器验证聊天与设置双向同步、刷新后的服务端配置、缺密钥提示、保存失败与重复入口移除。
未配置真实千问密钥时，不声称已通过真实模型调用或模型质量评测。

官方资料：
- https://platform.qianwenai.com/docs/developer-guides/getting-started/introduction
- https://platform.qianwenai.com/docs/developer-guides/tool-calling/function-calling
- https://platform.qianwenai.com/pricing/api
