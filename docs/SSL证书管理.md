# SSL 证书管理（ai.legouhou.cn）

> 申请时间：2026-08-23
> 申请工具：acme.sh（v3.1.5）
> 申请方式：HTTP 文件验证（Let's Encrypt）

## 证书信息

| 项 | 值 |
|---|---|
| 域名 | `ai.legouhou.cn` |
| 颁发者 | Let's Encrypt（O = Let's Encrypt, CN = YE2） |
| 证书类型 | ECC |
| 申请时间 | 2026-08-23 12:33:04 CST |
| 有效期 | 2026-08-23 → 2026-11-21（共 90 天） |
| 首次续签窗口 | 2026-10-22（acme.sh ARI 推荐） |
| 自动续签 | ✅ crontab 已装，每天 4 次检查 |

## 证书文件位置（云服务器 8.137.70.163）

**Nginx 实际使用的位置**（由 acme.sh 在续签时自动覆盖更新）：

```
/www/server/panel/vhost/cert/ai.legouhou.cn.crt    # 公钥证书（含全链）4812 字节
/www/server/panel/vhost/cert/ai.legouhou.cn.key    # 私钥 227 字节
```

**acme.sh 原始副本**（不动，acme.sh 内部管理）：

```
/root/.acme.sh/ai.legouhou.cn_ecc/ai.legouhou.cn.cer    # 原始证书
/root/.acme.sh/ai.legouhou.cn_ecc/ai.legouhou.cn.key    # 原始私钥
/root/.acme.sh/ai.legouhou.cn_ecc/fullchain.cer         # 完整证书链
/root/.acme.sh/ai.legouhou.cn_ecc/ca.cer                # 中间 CA
```

**⚠️ 安全注意**：`.key` 私钥是核心机密，**严禁明文写入项目文档、git commit、云同步**。泄漏意味着 HTTPS 完全被攻破。

## 续签

### 自动续签（推荐，不用管）

acme.sh 安装时已自动加 crontab 任务：

```bash
crontab -l | grep acme
# 22 1,7,13,19 * * * "/root/.acme.sh"/acme.sh --cron --home "/root/.acme.sh" > /dev/null
```

证书剩余有效期 < 60 天时自动续签，续签后自动 `nginx -s reload`，业务无感知。

### 手动续签（强制立即续）

```bash
~/.acme.sh/acme.sh --renew -d ai.legouhou.cn --force
```

### 查看证书列表和到期时间

```bash
~/.acme.sh/acme.sh --list
```

## 验证证书状态

### 在云服务器终端验证

```bash
# 1. HTTPS 是否能访问
curl -I https://ai.legouhou.cn
# 预期：HTTP/1.1 200 OK（前端首页）

# 2. 证书颁发者和有效期
echo | openssl s_client -connect ai.legouhou.cn:443 -servername ai.legouhou.cn 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
# 预期：issuer = "O = Let's Encrypt, CN = YE2"
#       notBefore / notAfter 显示有效期

# 3. /api/ 反代是否正常
curl -I https://ai.legouhou.cn/api/auth/login
# 预期：405 Method Not Allowed + allow: POST（FastAPI 正常响应）
```

### 在浏览器验证

打开 `https://ai.legouhou.cn`，看地址栏：
- ✅ 🔒 锁图标 = 证书有效
- 点锁 → "连接是安全的" → "证书有效" → 看颁发者和有效期

## 重新申请（如证书丢失/服务器重装）

完整 6 步流程见 `docs/frp隧道排障交接.md` 第七节。

简述：
1. 备份当前 Nginx conf
2. 安装 acme.sh（一次性）
3. 临时简化 conf（去掉 /api/ 段，保留 ACME 验证段）
4. `nginx -t && nginx -s reload`
5. `~/.acme.sh/acme.sh --issue -d ai.legouhou.cn --webroot /www/wwwroot/BoliAi/frontend --server letsencrypt`
6. `~/.acme.sh/acme.sh --install-cert ... --reloadcmd "nginx -s reload"`
7. 还原 conf + 加 443 段

## 关键配置片段（参考）

Nginx 443 server 块（`/www/server/panel/vhost/nginx/ai.legouhou.cn.conf`）：

```nginx
server {
    listen 443 ssl;
    server_name ai.legouhou.cn;
    
    ssl_certificate     /www/server/panel/vhost/cert/ai.legouhou.cn.crt;
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
```

80 server 块（强制跳 HTTPS，但保留 ACME 验证路径）：

```nginx
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
```

## 申请时走 acme.sh 而非宝塔的原因

宝塔"网站 → SSL → Let's Encrypt"申请时拒绝文件验证：

> 当前项目的服务（Nginx）配置文件被修改不支持文件验证，请选择其他方式或还原配置文件

原因：Nginx 主配置里 `/api/` 反代是手动写的（不走宝塔"反向代理"功能），宝塔认为整个 conf "非标"，拒绝文件验证。

走宝塔的修复路径要删手写 /api/ 段、重新登记反代、处理 site.db 冲突，风险面广。acme.sh 路线（临时简化 conf 30-60 秒 → 申请 → 还原 + 加 443 段）业务中断可控、不动宝塔内部状态，**省事且安全**。
