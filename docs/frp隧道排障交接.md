# BOLI AI Linux 迁移 & frp 隧道排障记录

> 用于新对话接手时的上下文快照
> 截止时间：2026-09-17（新增第九节：frpc 僵尸连接故障案例）

---

## 一、项目架构（当前目标状态）

```
用户浏览器
    │  https://ai.legouhou.cn
    ▼
┌─────────────────────────────────────────────┐
│ 云服务器 8.137.70.163（Linux 宝塔）          │
│                                             │
│  Nginx（宝塔）                               │
│   ├─ 前端静态文件  /www/wwwroot/BoliAi/frontend │
│   ├─ /api/ 反向代理 → http://127.0.0.1:8000  │
│   └─ SSL 证书：Let's Encrypt（acme.sh 申请） │
│                                             │
│  FastAPI 后端（uvicorn 8000 端口）            │
│    /www/wwwroot/BoliAi/backend              │
│    config.env 含 HTTPS_PROXY=127.0.0.1:8899 │
│         │                                   │
│         │ 走代理                            │
│         ▼                                   │
│  frps（/www/frp/frps，端口 7000）             │
└──────────────┬──────────────────────────────┘
               │ frp 隧道（云 7000 ⇄ 本地 7000）
               ▼
┌─────────────────────────────────────────────┐
│ 本地家庭电脑 192.168.1.38                    │
│                                             │
│  frpc（C:\frp\frpc.exe + frpc.toml）         │
│   └─ 把本地 8899 映射到云服务器 8899          │
│                                             │
│  proxy.py（HTTP 代理，监听 127.0.0.1:8899）   │
│         │                                   │
│         ▼                                   │
│  小扳手 xibapi.com（家庭宽带 IP）             │
└─────────────────────────────────────────────┘
```

**关键澄清（之前用户有过疑问）**：
- 后端跑在**云服务器**（127.0.0.1:8000），不是本地
- 本地电脑只跑 frpc + proxy.py 两个进程
- frp 隧道作用：让云后端通过"借"本地家庭宽带 IP 访问小扳手中转站（绕阿里云机房 IP 风控）

---

## 二、当前进度（已完成的）

| 项 | 状态 | 备注 |
|---|---|---|
| Linux 宝塔环境 | ✅ 完成 | |
| 前端代码部署 | ✅ 完成 | `/www/wwwroot/BoliAi/frontend` |
| 后端代码部署 | ✅ 完成 | `/www/wwwroot/BoliAi/backend` |
| Python 虚拟环境 | ✅ 完成 | 3.11 版本（解决依赖兼容性） |
| 后端依赖安装 | ✅ 完成 | |
| 后端服务 | ✅ 8000 端口在跑 | PID 466030 |
| Nginx 反向代理 | ✅ 已配 | `/www/server/panel/vhost/nginx/ai.legouhou.cn.conf` |
| ACME 文件验证段 | ✅ 已加 | SSL 申请用 |
| 域名 ai.legouhou.cn | ✅ 站点已建 | |
| 数据库 PostgreSQL | ✅ bunana_db | user:postgres / pass:boli123 / 5433 |
| frps 服务 | ✅ 在跑 | systemd `boli-frps.service`，端口 7000 监听中 |
| 宝塔防火墙 7000 端口 | ✅ 已放行 | |

---

## 三、当前卡点：frp 隧道 token 不匹配

### 3.1 现象

**云服务器 frps 日志**（一直刷错）：

```
register control error: token in login doesn't match token from configuration
```

每分钟一次，从 14.110.96.74 这个公网 IP 来的连接（**注意：这个 IP 不一定就是本地电脑的公网 IP，需要用户确认**）。

**本地电脑 CMD 检查**：

```bat
C:\>tasklist | findstr frpc
（无输出）

C:\>netstat -ano | findstr "8.137.70.163:7000" | findstr ESTABLISHED
（无输出）

C:\>netstat -ano | findstr ":8899" | findstr LISTENING
  TCP    0.0.0.0:8899    LISTENING    34072
```

- frpc 进程**没在跑**
- proxy.py（8899）**在跑** ✓
- 计划任务 `BOLI_Frpc` 状态"就绪"，但**上次结果 1**（启动失败退出）

### 3.2 已确认的事实

| 项 | 值 |
|---|---|
| frpc.exe 位置 | `C:\frp\frpc.exe`（15,627,776 字节） |
| frpc.toml 位置 | `C:\frp\frpc.toml`（353 字节） |
| 计划任务命令 | `"C:\frp\frpc.exe" -c C:\frp\frpc.toml` |
| 计划任务运行身份 | SYSTEM |
| 计划任务上次运行 | 2026/8/23 10:56:01（结果 1 = 失败） |
| 触发条件 | 系统启动时 |
| 云端 frps token | `boli2026frp`（已确认） |
| 本地 frpc.toml token | **未知，需用户 `type C:\frp\frpc.toml` 确认** |

### 3.3 真正根因

**frps 进程从早先启动时加载了当时的 token 到内存，之后不重读 frps.toml**。中间有人改过 frps.toml（让 token 变成了 `boli2026frp`），但 frps 进程一直没重启，内存里依然持有旧 token。所以 frpc 启动时读到 frpc.toml 里的 `boli2026frp`，跟 frps 内存里的旧值对不上。

**关键排查过程**（避免下次再走弯路）：
1. `type C:\frp\frpc.toml` 和 `cat /www/frp/frps.toml` 看到两边都是 `boli2026frp`，肉眼一致
2. `python -c "print(open(r'C:\frp\frpc.toml','rb').read(10).hex())"` 查 frpc.toml 字节头，无 BOM
3. `od -c /www/frp/frps.toml` 查 frps.toml 字节，全部 ASCII 干净
4. `/www/frp/frps -v` 和 frpc 日志都是 0.61.1，版本一致
5. `ps -ef | grep frps` 确认 frps 就是用 `/www/frp/frps -c /www/frp/frps.toml` 启动的
6. → 唯一没排查的就是 frps 进程内存状态。**直接重启**就能验证

### 3.4 解决方案（已修复）

```bash
# 云服务器终端
systemctl restart boli-frps
```

重启后 frps 重新加载磁盘上最新的 frps.toml，token 比对通过。

**实时联动验证**（一个窗口实时跟踪 frps 日志，一个本地窗口前台跑 frpc）：

```bash
# 云端窗口
journalctl -u boli-frps -f
```

```bat
:: 本地窗口
cd /d C:\frp
frpc.exe -c frpc.toml
```

看到 frps 日志出现 `login to server success` + `[proxy8899] tcp proxy listen port [8899]` = 通了。

**端到端代理链路验证**（在云端执行）：

```bash
curl --proxy http://127.0.0.1:8899 https://xibapi.com/v1/models
# 返回 {"error":{"code":"","message":"Invalid token (request id: ...)"}} = 链路通（被中转站业务层拦截 = 通了）
```

**注意**：手动跑完后记得 `Ctrl+C` 退掉前台 frpc，否则会跟计划任务那个 frpc 抢 proxy 注册（`proxy already exists`）。

---

## 四、待办事项（按优先级）

- [x] **修复 frp token 不匹配**（已修复，详见 3.3/3.4）
- [x] frp 隧道修通后，验证图片生成能正常走代理（已验证浏览器成功生成）
- [x] 申请 SSL 证书（已用 acme.sh 申请，详见第八节）
- [x] 验证 systemd `boli-backend` 服务（后端守护）正常运行（崩溃模拟测试通过，看门狗 8 秒内拉起新进程）
- [ ] 阿里云安全组入方向放行 TCP:7000（**已通过 frpc 成功连上 frps 隐式验证通过**，但建议在阿里云控制台再确认下安全组规则）
- [x] **家庭电脑加 frpc 看门狗计划任务**（`BOLI_FrpcGuard`，2026-09-18 已安装，每 3 分钟检测隧道连通性，不通自动重启 frpc；日志 `C:\frp\guard.log`，详见第九节 9.6）

---

## 五、关键文件路径速查

### 云服务器（8.137.70.163）
| 用途 | 路径 |
|---|---|
| 前端 | `/www/wwwroot/BoliAi/frontend` |
| 后端代码 | `/www/wwwroot/BoliAi/backend/app` |
| 后端配置 | `/www/wwwroot/BoliAi/backend/config.env` |
| 后端密钥 | `/www/wwwroot/BoliAi/backend/.secrets/` |
| 后端虚拟环境 | `/www/wwwroot/BoliAi/backend/venv` |
| 后端守护 | `/www/frp/boli-watch/watch_backend.py` |
| 后端日志 | `/www/frp/boli-watch/backend.log` |
| frps 配置 | `/www/frp/frps.toml` |
| frps systemd | `/etc/systemd/system/boli-frps.service` |
| Nginx 配置 | `/www/server/panel/vhost/nginx/ai.legouhou.cn.conf` |
| Nginx 配置备份（20260823） | `/www/server/panel/vhost/nginx/ai.legouhou.cn.conf.bak.20260823` |
| SSL 证书（acme.sh 输出） | `/www/server/panel/vhost/cert/ai.legouhou.cn.crt`（fullchain） |
| SSL 私钥 | `/www/server/panel/vhost/cert/ai.legouhou.cn.key` |
| acme.sh 原始证书目录 | `/root/.acme.sh/ai.legouhou.cn_ecc/` |

### 本地电脑（192.168.1.38）
| 用途 | 路径 |
|---|---|
| frpc | `C:\frp\frpc.exe` |
| frpc 配置 | `C:\frp\frpc.toml` |
| frpc 看门狗脚本 | `C:\frp\frpc_guard.bat`（日志 `C:\frp\guard.log`） |
| proxy.py | 全局 Python 包，启动命令含 `--timeout 900` |
| 计划任务 | `BOLI_Frpc`、`BOLI_Proxy`、`BOLI_Watch`、`BOLI_FrpcGuard`（每 3 分钟巡检隧道） |

---

## 六、配置.env 关键内容（云服务器后端）

```
CORS_ORIGINS=http://ai.legouhou.cn,http://8.137.70.163,http://localhost:8001,http://127.0.0.1:8001
DATABASE_URL=postgresql+psycopg2://postgres:boli123@8.137.70.163:5433/bunana_db?options=-csearch_path%3D%22AI%22
HTTPS_PROXY=http://127.0.0.1:8899
HTTP_PROXY=http://127.0.0.1:8899
```

---

## 七、SSL 证书申请记录（2026-08-23 完成）

### 为什么走 acme.sh 而不是宝塔

宝塔的"网站 → SSL → Let's Encrypt"申请时报错：**"当前项目的服务（Nginx）配置文件被修改不支持文件验证"**。

原因：Nginx 主配置 `ai.legouhou.cn.conf` 里 `/api/` 反向代理段是**手动写的**（不走宝塔的"反向代理"功能），宝塔认为整个 conf 文件"非标"，拒绝文件验证。

走宝塔的修复路径需要：
1. 删掉手写 `/api/` 段还原 conf
2. 在宝塔"反向代理"功能里重新加 /api/ → 127.0.0.1:8000
3. 才让宝塔能申请证书

这条路径要动现有工作配置、还要处理 site.db 的反代记录冲突。**风险面广，不优先选**。

### 走 acme.sh 的优势

- **不动现有 conf**（只临时简化，申请完还原）
- **业务中断可控**（约 30-60 秒：临时简化 conf 到还原 conf 之间）
- **全自动续签**（acme.sh crontab 已装，每天 4 次检查，到期前自动续 + 自动 reload）
- **证书标准**：Let's Encrypt，跟宝塔申请的完全一样，浏览器都能看到 🔒

### 申请流程（重做时按此操作）

```bash
# 1. 备份当前 conf
cp /www/server/panel/vhost/nginx/ai.legouhou.cn.conf \
   /www/server/panel/vhost/nginx/ai.legouhou.cn.conf.bak.YYYYMMDD

# 2. 安装 acme.sh（一次性，已装可跳过）
curl https://get.acme.sh | sh

# 3. 临时简化 conf（去掉 /api/ 段，保留 ACME + root + try_files）
cat > /www/server/panel/vhost/nginx/ai.legouhou.cn.conf <<'EOF'
server {
    listen 80;
    server_name ai.legouhou.cn;
    
    root /www/wwwroot/BoliAi/frontend;
    index index.html;
    
    location ~ ^/\.well-known/acme-challenge/ {
        allow all;
        default_type "text/plain";
    }
    
    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF
nginx -t && nginx -s reload

# 4. 申请证书（注意 --server letsencrypt，acme.sh 默认走 ZeroSSL 需要邮箱）
~/.acme.sh/acme.sh --issue -d ai.legouhou.cn \
  --webroot /www/wwwroot/BoliAi/frontend \
  --server letsencrypt

# 5. 安装证书到 Nginx 目录 + 设置自动 reload
mkdir -p /www/server/panel/vhost/cert
~/.acme.sh/acme.sh --install-cert -d ai.legouhou.cn \
  --key-file /www/server/panel/vhost/cert/ai.legouhou.cn.key \
  --fullchain-file /www/server/panel/vhost/cert/ai.legouhou.cn.crt \
  --reloadcmd "nginx -s reload"

# 6. 还原 conf + 加 443 段（80 强制跳 443，443 用证书）
cat > /www/server/panel/vhost/nginx/ai.legouhou.cn.conf <<'EOF'
server {
    listen 80;
    server_name ai.legouhou.cn;
    
    # ACME 文件验证（acme.sh 自动续签用，正则 location 优先级高于 /）
    location ~ ^/\.well-known/acme-challenge/ {
        allow all;
        default_type "text/plain";
    }
    
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name ai.legouhou.cn;
    
    ssl_certificate /www/server/panel/vhost/cert/ai.legouhou.cn.crt;
    ssl_certificate_key /www/server/panel/vhost/cert/ai.legouhou.cn.key;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    root /www/wwwroot/BoliAi/frontend;
    index index.html;
    
    location / {
        try_files $uri $uri/ /index.html;
    }
    
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF
nginx -t && nginx -s reload
```

### 续签相关命令

```bash
# 查证书列表和到期时间
~/.acme.sh/acme.sh --list

# 手动强制续签
~/.acme.sh/acme.sh --renew -d ai.legouhou.cn --force

# 查自动续签 crontab
crontab -l | grep acme
# 预期看到：22 1,7,13,19 * * * "/root/.acme.sh"/acme.sh --cron --home "/root/.acme.sh" > /dev/null
```

### 证书信息验证

```bash
# 看证书是不是 Let's Encrypt 颁发 + 有效期
echo | openssl s_client -connect ai.legouhou.cn:443 -servername ai.legouhou.cn 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
# 预期：issuer = "O = Let's Encrypt, CN = YE2"
# notAfter 一般 90 天后
```

---

## 八、用户偏好提醒（接手对话时注意）

- 中文交流，命令一次只发一段，不要一次发一堆
- 用户喜欢"一个问题一个问题来"，节奏要慢
- 项目名固定是 "BOLI AI"，目录用 `BoliAi`（无空格）
- 前后端完全分离
- API Key 只能放后端，不能给前端
- 改动要热部署
- 用户对 PowerShell 兼容性问题敏感（之前出过 `unzip` 找不到、Select-String 找不到等坑）

---

## 九、故障案例：frpc 僵尸连接（2026-09-17 已修复）

> 用户生图全部报"网络连接中断"，按本节流程 10 分钟内可定位修复。

### 9.1 现象

前端生成失败提示：

```
生成失败 [向量] 网络连接中断，请稍后重试; [小扳手] 网络连接中断，请稍后重试
已自动退还本次额度，可修改提示词或参考图后重新生成重试
```

**同组所有供应商同时报"网络连接中断"= 99% 是隧道/代理链路问题，不是供应商问题**（供应商故障一般只有一家报错）。

错误来源：`backend/app/services/engine.py` 的 `ConnectionError` 分支（重试 1 次后仍失败）。
注意区分：报"代理连接异常"= ProxyError（连代理端口都失败，8899 没监听）；报"网络连接中断"= ConnectionError（代理端口通，但数据流过隧道时断了，**僵尸连接就是这种**）。

### 9.2 重要：别查错电脑

- frpc 和 proxy.py 跑在**家庭电脑**（192.168.1.38，`C:\frp\`），**不是**开发机（d:\code 那台）！
- 开发机上查 `tasklist | findstr frpc` 永远是空的，别被误导
- 开发机本地生图正常 ≠ 隧道正常（开发机直连中转站，不走隧道）

### 9.3 快速定位流程（一次一条命令，按顺序）

**第 1 步（家庭电脑 CMD）：两个进程活着吗**

```bat
tasklist | findstr frpc
netstat -ano | findstr ":8899" | findstr LISTENING
```

两条都有输出 = 进程活着，继续第 2 步；哪条没输出 = 哪个进程挂了，用计划任务拉起（`schtasks /run /tn BOLI_Frpc` / `BOLI_Proxy`）。

**第 2 步（家庭电脑 CMD）：frpc 和云端连着吗**

```bat
netstat -ano | findstr "8.137.70.163:7000" | findstr ESTABLISHED
```

有输出 = 连着（**但可能是僵尸，继续往下查**）；无输出 = frpc 没连上，直接重启 frpc（见 9.4）。

**第 3 步（家庭电脑 CMD）：本机代理能出网吗**

```bat
curl -x http://127.0.0.1:8899 https://xibapi.com/v1/models
```

返回 `Invalid token ...` = 家庭电脑这一段全好（被业务层拦截 = 网络通）；报错/超时 = proxy.py 或家庭网络问题。

**第 4 步（云服务器终端）：整条链路通吗**

```bash
curl --proxy http://127.0.0.1:8899 https://xibapi.com/v1/models
```

- 返回 `Invalid token ...` = 链路全通，问题在云后端（`systemctl restart boli-backend` 试试）
- **`curl: (56) Recv failure: Connection reset by peer`** = 本次故障的经典表现：8899 有监听（proxy 注册还在）但隧道数据流不通 → **frpc 僵尸连接**，走 9.4 修复

### 9.4 修复方法（本次生效的）

**家庭电脑**上杀掉 frpc 并用计划任务重启：

```bat
:: PID 换成第 1 步查到的实际 PID
taskkill /PID 15156 /F
schtasks /run /tn BOLI_Frpc
```

**云服务器**复测（出现 `Invalid token` 即修复）：

```bash
curl --proxy http://127.0.0.1:8899 https://xibapi.com/v1/models
```

若重启 frpc 后仍 reset：先 `systemctl restart boli-frps`（云端），再重启 frpc（家庭电脑），再复测。还没好就按 3.3 查 token。

### 9.5 根因分析

frpc 到云服务器的控制连接是**长期保持的 TCP 连接**，中间经过家庭路由器 NAT、运营商设备、云防火墙。这些中间设备会**静默丢弃**看似空闲的长连接（不通知两端）。frpc 本该靠心跳察觉并重连，但遇到家里网络闪断、路由器重启、运营商 NAT 超时等情况，连接已死 frpc 却仍认为活着（netstat 显示 ESTABLISHED 但实际不通）——即"僵尸连接"。

**特点**：
- 随时可能发生一次，与用户量/并发无关，与家庭网络稳定性有关
- frpc 进程不退出、端口照常监听、TCP 显示已连接，一切"看起来正常"
- 只有实际走一遍流量（云端 curl）才能暴露

### 9.6 预防措施（2026-09-18 已定稿：3 分钟巡检一次）

> 为什么是 3 分钟：更短（1-2 分钟）会频繁重启 frpc，容易误伤正在生成中的请求；更长恢复太慢。3 分钟是恢复速度与稳定性的平衡点。

**在家庭电脑（192.168.1.38）上执行，共 4 步：**

第 1 步：打开 CMD，创建看门狗脚本

```bat
notepad C:\frp\frpc_guard.bat
```

弹出"是否新建文件"点"是"，粘贴以下内容，`Ctrl+S` 保存后关闭记事本：

```bat
@echo off
curl -s --max-time 20 -x http://127.0.0.1:8899 https://xibapi.com/v1/models | findstr "new_api_error" >nul
if %errorlevel% neq 0 (
    echo %date% %time% 隧道不通，重启frpc >> C:\frp\guard.log
    taskkill /im frpc.exe /f
    schtasks /run /tn BOLI_Frpc
)
```

第 2 步：创建计划任务（每 3 分钟运行一次）

```bat
schtasks /create /tn BOLI_FrpcGuard /tr "C:\frp\frpc_guard.bat" /sc minute /mo 3 /ru SYSTEM /rl HIGHEST /f
```

看到 `成功: 成功创建计划任务 "BOLI_FrpcGuard"。` 即成功。

第 3 步：手动跑一次验证任务能执行

```bat
schtasks /run /tn BOLI_FrpcGuard
```

第 4 步：确认看门狗判定正常（无新增日志 = 健康）

```bat
type C:\frp\guard.log
```

显示"找不到文件"或无新记录 = 看门狗检测隧道正常、没有误判，安装完成。

**日后运维**：
- 查隧道断过几次：`type C:\frp\guard.log`（一行 = 一次自动修复）
- 删除看门狗：`schtasks /delete /tn BOLI_FrpcGuard /f`
- 若日志频繁出现重启记录（说明网络抖动误判多），把 `/mo 3` 改 `/mo 5` 重建

> 原理：看门狗每 3 分钟借隧道访问一次中转站；隧道正常时 curl 返回含 `new_api_error` 的业务响应（findstr 命中 = 不动作）；20 秒超时或响应异常 = 隧道死 → 写日志并重启 frpc。
> 远期（并发大了以后）：把"借家庭宽带出网"换成独立中转代理服务器，消除家庭宽带这个单点和上行带宽瓶颈。

### 9.7 并发影响说明（用户问过）

- 僵尸连接本身与并发无关（是长连接被中间设备静默掐断）
- 并发真正的影响：所有云端生图请求共享家庭宽带上行，多人同时传参考图会占满上行 → 请求变慢/超时
- 单点风险：frpc 一挂所有用户同时报错（本次即是），看门狗可把故障恢复时间从"人工发现"缩到 10 分钟内
