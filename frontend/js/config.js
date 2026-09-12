/* ============================================================
   API 调用封装
   ============================================================ */

// API_BASE 可配置：
//  - 不设置（undefined/null）：本地开发默认 http://localhost:8000；生产（非 localhost/127.0.0.1）自动走同源反代 /api
//  - 部署时由 Nginx 同源反代 /api：注入 window.API_BASE = ''（同源相对路径，无跨域）
//  - 独立域名：注入 window.API_BASE = 'https://api.example.com'
const API_BASE = (window.API_BASE !== undefined && window.API_BASE !== null)
  ? String(window.API_BASE).replace(/\/+$/, '')
  : (location.hostname === 'localhost' || location.hostname === '127.0.0.1' ? 'http://localhost:8000' : '');
const TOKEN_KEY = 'dongli_token';
const USER_KEY = 'dongli_user';

async function request(path, options = {}) {
  const headers = options.headers || {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;

  if (!(options.body instanceof FormData) && options.body && typeof options.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }

  try {
    const res = await fetch(API_BASE + path, { ...options, headers });
    if (res.status === 401) {
      // 登录/注册接口：401 是业务错误（密码错误、账号不存在等），透传后端具体信息
      if (path.includes('/api/auth/')) {
        const data = await res.json().catch(() => null);
        throw new Error((data && data.detail) || '账号或密码错误');
      }
      // 其他接口：401 表示会话过期，清除登录态并跳转登录页
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      if (!location.pathname.includes('login.html') && !location.pathname.includes('register.html')) {
        location.href = 'login.html';
      }
      throw new Error('请先登录');
    }
    const contentType = res.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await res.json() : await res.text();
    if (!res.ok) {
      const msg = (data && data.detail) || (typeof data === 'string' ? data : `HTTP ${res.status}`);
      throw new Error(msg);
    }
    return data;
  } catch (err) {
    if (err.name === 'TypeError' && err.message.includes('Failed to fetch')) {
      throw new Error('无法连接到后端服务器，请确认后端已启动');
    }
    // 翻译常见 JS 错误为中文
    const translation = {
      'Assignment to constant variable': '程序内部错误：常量被重新赋值',
      'Failed to fetch': '网络请求失败',
      'NetworkError': '网络错误',
      'Unexpected token': '服务器返回数据格式异常',
    };
    for (const [en, zh] of Object.entries(translation)) {
      if (err.message && err.message.includes(en)) {
        throw new Error(zh);
      }
    }
    throw err;
  }
}

const api = {
  // 认证
  register: (account, password) =>
    request('/api/auth/register', { method: 'POST', body: { account, password } }),
  login: (account, password) =>
    request('/api/auth/login', { method: 'POST', body: { account, password } }),
  profile: () => request('/api/user/profile'),

  // 文件上传
  upload: (file) => {
    const fd = new FormData();
    fd.append('file', file);
    return request('/api/upload', { method: 'POST', body: fd });
  },
  uploadZip: (file) => {
    const fd = new FormData();
    fd.append('file', file);
    return request('/api/upload/zip', { method: 'POST', body: fd });
  },
  // 删除已上传的参考图文件（参考图被移除 / 页面刷新 / 关闭时调用）
  deleteUpload: (path) =>
    request('/api/upload?path=' + encodeURIComponent(path), { method: 'DELETE' }),

  // 任务
  createTask: (data) => request('/api/tasks', { method: 'POST', body: data }),
  listTasks: () => request('/api/tasks'),
  getTask: (id) => request(`/api/tasks/${id}`),
  getTaskResults: (id) => request(`/api/tasks/${id}/results`),
  executeTask: (id) => request(`/api/tasks/${id}/execute`, { method: 'POST' }),

  // 文件
  fileUrl: (taskId, seq) => `${API_BASE}/api/files/${taskId}/${seq}`,
};

window.api = api;

// ============================================================
// 主题：固定亮色模式（不提供暗色切换）
// ============================================================
document.documentElement.setAttribute('data-theme', 'light');
