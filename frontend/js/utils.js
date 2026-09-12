/* ============================================================
   工具函数
   ============================================================ */

function toast(msg, type = 'info', duration = 2500) {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300);
  }, duration);
}

function statusText(s) {
  return {
    pending: '等待中',
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    partial: '部分完成',
  }[s] || s;
}

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso.replace(' ', 'T'));
  if (isNaN(d)) return iso;
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function firstChar(s) { return (s || '?').charAt(0).toUpperCase(); }

window.toast = toast;
window.statusText = statusText;
window.formatTime = formatTime;
window.escapeHtml = escapeHtml;
window.firstChar = firstChar;
