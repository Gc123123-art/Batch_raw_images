# BOLI AI 批量图片生成平台 — 前端技术架构

## 1. 技术选型

| 维度 | 选择 | 理由 |
|------|------|------|
| 形态 | 纯静态前端（HTML + CSS + JS） | 与 Python 后端完全解耦，可独立部署 |
| 构建 | 无构建工具，CDN 引入 | 零配置，可直接放到任何静态服务器 |
| 框架 | 原生 JS (ES6+) | 功能不复杂，无需引入 React/Vue |
| 样式 | 原生 CSS + CSS 变量 | 控制权最强，不被框架约束 |
| HTTP | `fetch` API | 原生支持 |
| 部署 | 可部署到 Vercel / Netlify / Nginx / 任意静态服务器 | 灵活 |

## 2. 目录结构

```
web_frontend/
├── index.html              # 入口（重定向到登录或仪表盘）
├── login.html              # 登录页
├── register.html           # 注册页
├── dashboard.html          # 仪表盘（核心页面）
├── task-detail.html        # 任务详情页
├── css/
│   └── style.css           # 全局样式
├── js/
│   ├── api.js              # API 调用封装
│   ├── auth.js             # 认证状态管理
│   └── utils.js            # 工具函数
└── assets/
    └── logo.svg            # Logo
```

## 3. 与后端的 API 对接

| 用途 | API |
|------|------|
| 注册 | `POST /api/auth/register` |
| 登录 | `POST /api/auth/login` |
| 用户信息 | `GET /api/user/profile` |
| 上传文件 | `POST /api/upload` |
| 上传 ZIP | `POST /api/upload/zip` |
| 创建任务 | `POST /api/tasks` |
| 任务列表 | `GET /api/tasks` |
| 任务详情 | `GET /api/tasks/{id}` |
| 任务结果 | `GET /api/tasks/{id}/results` |
| 执行任务 | `POST /api/tasks/{id}/execute` |
| 查看图片 | `GET /api/files/{task_id}/{filename}` |

## 4. 关键技术点

### 4.1 Token 管理
- 登录后把 JWT 存到 `localStorage`
- 所有需要认证的请求从 `localStorage` 读 token，放到 `Authorization: Bearer xxx` 头
- 拦截 401 响应，自动跳到登录页

### 4.2 轮询进度
- 任务详情页用 `setInterval` 每 2 秒调用 `GET /api/tasks/{id}`
- 当 status 变成 `completed` / `failed` / `partial` 时停止轮询
- 切换 tab 时停止轮询节省请求

### 4.3 文件上传
- 用 `FormData` 提交
- 进度条用 `XMLHttpRequest` 监听 `upload.onprogress`

### 4.4 跨域处理
- 后端已配置 CORS 允许所有 origin
- 如生产环境需要限制，改成具体域名

## 5. 配置点

- 后端 API 地址：可放在 `js/api.js` 顶部一个 `const API_BASE = 'http://localhost:8001'`
- 部署到生产时改这一行即可

## 6. 部署方案

```nginx
# Nginx 示例
server {
    listen 80;
    server_name your-domain.com;
    root /var/www/web_frontend;
    index dashboard.html;
}
```

后端单独部署，监听另一端口（如 8000），用 Nginx 反向代理 `/api/` 转发到后端，实现同域访问避免 CORS。
