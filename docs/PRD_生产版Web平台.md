# BOLI AI 批量图片生成平台 — 生产版需求文档 (PRD)

## 1. 商业模式

**统一直充模式**：平台统一管理 DONGLI AI 的 API 调用，客户预充值后按次扣费，无需自己管理 API Key。

## 2. 版本规划

| 版本 | 目标 | 功能范围 |
|------|------|---------|
| **v1.0 MVP** | 可用的最小闭环 | 基础功能 |
| v1.1 | 完善体验 | 支付、管理后台 |
| v1.2 | 运营能力 | 套餐、优惠码 |

**本次先做 v1.0 MVP**，以下文档只覆盖 v1.0 范围。

## 3. v1.0 MVP 功能范围

### 3.1 用户认证

| 功能 | 说明 |
|------|------|
| 注册 | 账号+密码，密码 bcrypt 加密 |
| 登录 | JWT token 认证，24h 有效期 |
| Token 刷新 | 无过期前自动刷新 |

### 3.2 余额系统

| 功能 | 说明 |
|------|------|
| 注册送体验金 | 新用户注册赠送 10 次体验额度 |
| 余额查询 | 登录后可查看剩余次数 |
| 自动扣费 | 每次成功生成图片扣除 1 次额度 |
| 人工充值 | v1.0 先手动充值，v1.1 再接支付 |

### 3.3 批量生图（核心功能）

与现有 CLI 功能对齐：

| 功能 | 说明 |
|------|------|
| 文生图 | 填写 prompts，批量生成 |
| 图生图 | 上传参考图 + prompts，批量生成 |
| 文件夹模式 | 上传 zip 解压为文件夹，一个 prompt 对多张图 |
| 实时进度 | 轮询显示当前进度（X/Y 已完成） |
| 结果预览 | 生成后在线查看和下载 |

### 3.4 任务管理

| 功能 | 说明 |
|------|------|
| 任务列表 | 查看历史任务（时间、状态、数量） |
| 任务详情 | 查看每张生成结果 |
| 结果下载 | 单张下载 / 全部打包下载 |

### 3.5 API 设计

前后端完全分离，所有接口返回 JSON。

```
POST   /api/auth/register        # 注册
POST   /api/auth/login           # 登录
GET    /api/user/profile         # 用户信息（含余额）
GET    /api/user/balance         # 查询余额

POST   /api/tasks                # 创建批量任务
GET    /api/tasks                # 任务列表
GET    /api/tasks/{id}           # 任务详情（含进度）
GET    /api/tasks/{id}/results   # 任务生成结果
GET    /api/tasks/{id}/download  # 打包下载

POST   /api/admin/recharge       # (管理员) 为客户充值
```

## 4. 非功能性需求

| 项目 | 要求 |
|------|------|
| 前后端分离 | 前端纯静态可独立部署（Vercel/NGINX） |
| 数据库 | SQLite（起步）→ PostgreSQL（视规模迁移） |
| 密钥安全 | DONGLI AI Key 只存后端环境变量，不落数据库 |
| 文件存储 | 本地文件系统（可扩展为 S3/OSS） |
| 部署方式 | Docker Compose 一键部署 |

## 5. 不包含在 v1.0 的内容

- ❌ 微信/支付宝支付接入（v1.1）
- ❌ 套餐/会员体系（v1.1）
- ❌ 管理后台（v1.1）
- ❌ 优惠码系统（v1.2）
- ❌ 邮件通知（v1.2）
- ❌ 数据统计分析（v1.2）

## 6. 项目目录结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  ← FastAPI 主入口
│   ├── services/engine.py       ← 核心引擎（AI 调用 / 合成 / 故障转移）
│   ├── api/routes/              ← 路由层
│   ├── db/                      ← 数据库
│   └── schemas/                 ← Pydantic 模型
├── requirements.txt            ← 依赖
└── .secrets/                   ← 自动生成的密钥

frontend/
├── index.html                  ← 入口页面
├── css/
│   └── style.css
├── js/
│   ├── api.js                  ← 封装所有 API 调用
│   ├── auth.js                 ← 登录/注册逻辑
│   └── tasks.js                ← 任务管理逻辑
├── login.html
├── register.html
├── dashboard.html
└── task_detail.html
```
