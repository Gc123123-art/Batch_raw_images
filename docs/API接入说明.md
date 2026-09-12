# AI 中转站 API 接入说明

本项目使用 **OpenAI 兼容协议**，任何支持 `/v1/images/generations` 和 `/v1/images/edits` 接口的 AI 中转站都可以接入。

---

## 1. 你需要从中转站获取的信息

联系中转站客服或查看其文档，拿到以下 **3 项信息**：

| 信息 | 说明 | 示例 |
|------|------|------|
| **API 地址 (BASE_URL)** | 中转站提供的接口域名（**不要**带 `/v1` 后缀） | `https://api.xxx.com` |
| **API 密钥 (API_KEY)** | 你的身份凭证，通常以 `sk-` 开头 | `sk-xxxxxxxxxxxxxxxx` |
| **模型名 (MODEL)** | 中转站支持的图片生成模型名称 | `gpt-image-2` / `image2` / `dall-e-3` |

---

## 2. 配置 AI 供应商（数据库）

供应商信息（API 地址 / 密钥 / 模型 / 优先级）存储在数据库 `api_providers`
表中，通过管理员接口管理，不再使用 `config.env` 文件。

新增供应商（需管理员密钥 `X-Admin-Key`），**只需填写核心 3 项**：

```bash
# 示例：新增一个供应商
curl -X POST http://localhost:8000/api/admin/providers \
  -H "X-Admin-Key: <管理员密钥>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "向量 AI",
    "api_key": "sk-xxxxxxxxxxxxxxxx",
    "base_url": "https://api.xxx.com",
    "model": "gpt-image-2"
  }'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 供应商名称（唯一） |
| `api_key` | 是 | API 密钥（`sk-` 开头） |
| `base_url` | 是 | 接口地址（**不要**带 `/v1` 后缀） |
| `model` | 否 | 模型名（留空用默认） |

可选（一般不用管，有默认值）：`priority`（优先级，数字小的先尝试）、
`enabled`（是否启用）。图片尺寸、生成数量、超时时间为全局默认值，不随供应商配置。

其他接口：

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/api/admin/providers` | 列出全部供应商（key 脱敏） |
| PUT | `/api/admin/providers/{id}` | 更新供应商 |
| PATCH | `/api/admin/providers/{id}/enabled` | 启用 / 停用 |
| DELETE | `/api/admin/providers/{id}` | 删除供应商 |

> 首次启动时若 `api_providers` 表为空，会自动从旧 `config-*.env` 文件一次性导入。

---

## 3. 任务提交（Web 界面）

任务通过前端 Web 界面提交（注册登录后，在页面填写提示词、上传参考图、选择尺寸数量）。

> **命令行批量生成工具**（`tasks.csv` / `batch_generator.py`）已迁移至独立项目，
> 本平台不再提供 CLI 版本，仅提供 Web 界面。

---

## 4. 多供应商与故障转移

多个供应商配置在数据库 `api_providers` 表中，无需切换文件：

- 按 `priority` 升序依次尝试（数字小的优先）
- 当前供应商调用失败时自动切换到下一个
- 会记忆最近成功的供应商（`.api_memory.json`），下次优先使用
- 单个供应商失效不影响其他任务

---

## 5. 常见中转站配置参考

> 以下信息来自注释记录，实际以中转站官方文档为准。

| 中转站 | API_KEY 示例 | BASE_URL | 模型名 |
|--------|-------------|----------|--------|
| 向量 AI | `sk-fdge...` | `https://api.vectorengine.ai` | `gpt-image-2` |
| 小扳手 | `sk-9vrT...` | `https://xibapi.com` | `gpt-image-2-2K` / `image2` |
| DONGLI AI | `sk-dl-...` | 请咨询官方 | 请咨询官方 |

---

## 6. 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| HTTP 401 | API Key 错误 | 检查供应商 `api_key` 是否填对 |
| HTTP 402 | 余额不足 | 去中转站充值 |
| HTTP 403 | 模型无权限 | 确认模型名是否支持、是否在白名单 |
| HTTP 404 | 接口地址错误 | 检查供应商 `base_url` 是否填了完整域名（不要带 `/v1` 路径） |
| HTTP 429 | 请求频率过高 | 稍后重试，或减少同时提交的任务数 |
| 超时 | 模型生成速度慢 | 增加 `TIMEOUT=600` 或更高 |
| "未指定模型" | 缺少模型名 | 设置 `DEFAULT_MODEL` 或供应商配置 `model` |
