/* ============================================================
   认证状态管理
   ============================================================ */

const auth = {
  getToken() { return localStorage.getItem('dongli_token'); },
  getUser() {
    try { return JSON.parse(localStorage.getItem('dongli_user')); }
    catch { return null; }
  },
  setSession(token, user) {
    localStorage.setItem('dongli_token', token);
    localStorage.setItem('dongli_user', JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem('dongli_token');
    localStorage.removeItem('dongli_user');
  },
  isLoggedIn() { return !!this.getToken(); },
  requireLogin() {
    if (!this.isLoggedIn()) {
      location.href = 'login.html';
      return false;
    }
    return true;
  },
  async loadProfile() {
    try {
      const p = await api.profile();
      const u = { user_id: p.user_id, account: p.account, balance: p.balance };
      this.setSession(this.getToken(), u);
      return u;
    } catch (e) {
      this.clear();
      location.href = 'login.html';
      throw e;
    }
  },
  logout() {
    this.clear();
    location.href = 'login.html';
  }
};
window.auth = auth;
