# BOLI AI 批量图片生成平台

基于 **FastAPI + PostgreSQL + 原生前端** 的批量图片生成平台，支持：

- 用户注册/登录（JWT 认证，bcrypt 密码加密）
- 按生成图片张数扣费（每次扣 1 次额度，管理员充值）
- 文生图 / 图生图（支持 1-3 张参考图自动合成）
- 批量上传 ZIP/RAR 压缩包，每张参考图独立生成
- 多 API 供应商自动故障转移（数据库管理，失败自动切换并记忆）
- 前后端完全分离，可独立部署

---

## 目录结构

```
Batch_raw_images/
├── backend/                    # 后端（FastAPI）
│   ├── app/
│   │   ├── main.py             # 应用入口（uvicorn 启动）
│   │   ├── core/               # 配置 / 安全 / 密钥管理
│   │   ├── api/                # 路由层
│   │   │   ├── deps.py         # 认证依赖（JWT / Admin Key / 限流）
│   │   │   └── routes/         # auth / users / tasks / admin / upload
│   │   ├── db/                 # 数据访问层（PostgreSQL）
│   │   ├── schemas/            # Pydantic 请求模型
│   │   └── services/           # 核心引擎（AI 调用 / 合成 / 故障转移）
│   ├── venv/                   # Python 虚拟环境（不入库）
│   ├── requirements.txt
│   └── .secrets/               # 自动生成的密钥（不入库）
├── frontend/                   # 前端（纯 HTML/CSS/JS，独立部署）
│   ├── css/  js/  *.html
│   └── dev_server.py           # 本地开发热部署服务器
├── data/                       # 运行数据（不入库）
│   ├── .api_memory.json        # API 供应商记忆
│   └── uploads/                # 用户上传的参考图
├── deploy/                     # 生产部署配置
│   ├── nginx-https.conf        # Nginx HTTPS 反代配置
│   ├── dongli-api.service      # systemd 服务
│   └── env.example             # 生产环境变量模板
├── docs/                       # 需求与设计文档
├── start.bat                   # 本地一键启动（后端 + 前端）
└── .gitignore
```

---

## 本地开发

```bat
:: 一键启动（后端 8000 + 前端 8001 + 自动打开浏览器）
start.bat
```

或分别启动：

```bat
:: 后端
cd backend
.\venv\Scripts\python.exe -m app.main --port 8000

:: 前端（另一终端）
cd frontend
..\backend\venv\Scripts\python.exe dev_server.py 8001
```

访问 `http://localhost:8001`。

> 首次运行后需配置 AI 供应商：通过管理员接口（X-Admin-Key）在数据库中
> 新增供应商，详见下方「API 供应商管理」。

---

## API 供应商管理（数据库）

供应商配置（API_KEY / BASE_URL / 模型 / 优先级）存储在数据库 `api_providers`
表中，通过管理员接口管理（首次启动时若表为空，会自动从旧 `config-*.env` 一次性导入）：

| 方法 | 接口 | 说明 |
|------|------|------|
| GET | `/api/admin/providers` | 列出全部供应商（key 脱敏） |
| POST | `/api/admin/providers` | 新增供应商（只需 API_KEY / BASE_URL / model） |
| PUT | `/api/admin/providers/{id}` | 更新供应商 |
| PATCH | `/api/admin/providers/{id}/enabled` | 启用 / 停用 |
| DELETE | `/api/admin/providers/{id}` | 删除供应商 |

请求头需带 `X-Admin-Key: <管理员密钥>`。多供应商按 `priority` 升序尝试，
失败自动切换到下一个，并记忆最近成功的供应商（`.api_memory.json`）。

## 平台配置（环境变量）

所有平台配置均通过**环境变量**注入（本地开发用系统环境变量，生产用
systemd `EnvironmentFile`，见 [deploy/env.example](deploy/env.example)），
项目内不再有 `config.env` 文件：

| 变量 | 说明 |
|------|------|
| `CORS_ORIGINS` | 允许的前端域名（逗号分隔） |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | 登录限流 |
| `PREVIEW_SIG_TTL` | 上传预览签名有效期（秒） |
| `ADMIN_KEY` | 管理员密钥（必填，生产必须注入） |
| `JWT_SECRET` | JWT 签名密钥（必填，生产必须注入） |

> 供应商密钥已入库，不需要任何 `config-*.env` 文件。

---

## 生产部署

参考 [deploy/nginx-https.conf](deploy/nginx-https.conf) 与 [deploy/dongli-api.service](deploy/dongli-api.service)：

1. 上传代码到 `/opt/dongli-api`（`data/`、`venv/`、`.secrets/` 不入库）
2. 创建虚拟环境并安装依赖：`python3 -m venv backend/venv && backend/venv/bin/pip install -r backend/requirements.txt`
3. 复制 [deploy/env.example](deploy/env.example) 为 `/etc/dongli-api/env`（chmod 600）并填入密钥
4. 安装 systemd 服务并启动
5. 配置 Nginx HTTPS（certbot 签发证书）

## API 文档

后端启动后访问 `http://localhost:8000/docs` 查看 Swagger 交互文档。
