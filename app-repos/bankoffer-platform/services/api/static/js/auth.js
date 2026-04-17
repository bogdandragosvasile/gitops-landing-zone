/**
 * BankOffer AI — Authentication (Keycloak OIDC)
 * Manual PKCE authorization code flow — no dependency on keycloak-js adapter
 * for the critical auth code exchange.
 *
 * In DEMO_MODE (no Keycloak available), provides a mock auth flow
 * with role-based demo users.
 */
(function() {
  const STORAGE_KEY_BASE = 'boai_auth';
  const PKCE_KEY = 'boai_pkce';
  const KC_URL = 'https://auth.lupulup.com';
  const KC_REALM = 'bankofferai';
  const KC_CLIENT_ID = 'bankofferai-app';
  const KC_BASE = KC_URL + '/realms/' + KC_REALM + '/protocol/openid-connect';
  const TOKEN_URL = KC_BASE + '/token';
  const AUTH_URL = KC_BASE + '/auth';
  const LOGOUT_URL = KC_BASE + '/logout';

  // ---- Portal-scoped session isolation ----
  // List all hostnames that serve the customer portal (VM and K8s deployments).
  const CUSTOMER_DOMAINS = [
    'my-bankoffer.lupulup.com',
    'my-bankoffer-k8s.lupulup.com',
  ];
  const CUSTOMER_DOMAIN = CUSTOMER_DOMAINS[0]; // kept for logout redirect compat
  const EMPLOYEE_DOMAIN = 'bankoffer.lupulup.com';

  function _isCustomerDomain() {
    return CUSTOMER_DOMAINS.includes(window.location.hostname);
  }

  function _getPortalContext() {
    if (_isCustomerDomain()) return 'client';
    if (window.location.pathname.startsWith('/admin')) return 'admin';
    return 'employee';
  }

  function _getStorageKey() {
    return STORAGE_KEY_BASE + '_' + _getPortalContext();
  }

  // Demo users for when Keycloak is unavailable
  const DEMO_USERS = {
    admin: {
      sub: 'demo-admin-001',
      email: 'admin@bankofferai.com',
      name: 'System Administrator',
      roles: ['admin', 'employee'],
      customer_id: '0'
    },
    employee: {
      sub: 'demo-employee-001',
      email: 'manager@bankofferai.com',
      name: 'Relationship Manager',
      roles: ['employee'],
      customer_id: '0'
    },
    client: {
      sub: 'demo-client-001',
      email: 'client1@bankofferai.com',
      name: 'Demo Client',
      roles: ['client'],
      customer_id: '1'
    }
  };

  let _currentUser = null;
  let _demoMode = true;
  let _onAuthChange = [];
  let _accessToken = null;
  let _refreshToken = null;
  let _idToken = null;

  // ---- PKCE helpers ----
  function _generateRandomString(length) {
    const arr = new Uint8Array(length);
    crypto.getRandomValues(arr);
    return Array.from(arr, b => b.toString(16).padStart(2, '0')).join('').slice(0, length);
  }

  async function _generateCodeChallenge(verifier) {
    const encoder = new TextEncoder();
    const data = encoder.encode(verifier);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function _parseJwt(token) {
    try {
      const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(atob(base64));
    } catch(e) {
      console.error('[Auth] JWT parse error:', e);
      return null;
    }
  }

  // ---- SSO Login (manual PKCE flow) ----
  async function keycloakLogin() {
    const redirectUri = window.location.origin + window.location.pathname;
    const state = _generateRandomString(32);
    const nonce = _generateRandomString(32);
    const codeVerifier = _generateRandomString(64);
    const codeChallenge = await _generateCodeChallenge(codeVerifier);

    // Store PKCE state in sessionStorage (survives redirect, same tab only)
    sessionStorage.setItem(PKCE_KEY, JSON.stringify({
      state: state,
      nonce: nonce,
      codeVerifier: codeVerifier,
      redirectUri: redirectUri
    }));

    const params = new URLSearchParams({
      client_id: KC_CLIENT_ID,
      redirect_uri: redirectUri,
      response_type: 'code',
      response_mode: 'query',
      scope: 'openid email profile',
      state: state,
      nonce: nonce,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256'
    });

    console.log('[Auth] Redirecting to Keycloak for login...');
    window.location.href = AUTH_URL + '?' + params.toString();
  }

  async function keycloakRegister() {
    const redirectUri = window.location.origin + window.location.pathname;
    const state = _generateRandomString(32);
    const nonce = _generateRandomString(32);
    const codeVerifier = _generateRandomString(64);
    const codeChallenge = await _generateCodeChallenge(codeVerifier);

    sessionStorage.setItem(PKCE_KEY, JSON.stringify({
      state: state,
      nonce: nonce,
      codeVerifier: codeVerifier,
      redirectUri: redirectUri
    }));

    const params = new URLSearchParams({
      client_id: KC_CLIENT_ID,
      redirect_uri: redirectUri,
      response_type: 'code',
      response_mode: 'query',
      scope: 'openid email profile',
      state: state,
      nonce: nonce,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256'
    });

    window.location.href = KC_BASE + '/registrations?' + params.toString();
  }

  // ---- Auth code exchange ----
  async function _handleAuthCallback() {
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');
    const state = urlParams.get('state');

    if (!code || !state) return false;

    console.log('[Auth] Auth callback detected: code=' + code.substring(0, 8) + '... state=' + state.substring(0, 8) + '...');

    // Retrieve stored PKCE data
    const pkceRaw = sessionStorage.getItem(PKCE_KEY);
    if (!pkceRaw) {
      console.error('[Auth] No PKCE data in sessionStorage — cannot exchange code');
      return false;
    }

    const pkce = JSON.parse(pkceRaw);
    sessionStorage.removeItem(PKCE_KEY);

    // Validate state
    if (pkce.state !== state) {
      console.error('[Auth] State mismatch: expected=' + pkce.state.substring(0, 8) + ' got=' + state.substring(0, 8));
      return false;
    }

    // Exchange code for tokens
    console.log('[Auth] Exchanging auth code for tokens...');
    try {
      const resp = await fetch(TOKEN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: KC_CLIENT_ID,
          code: code,
          redirect_uri: pkce.redirectUri,
          code_verifier: pkce.codeVerifier
        }).toString()
      });

      if (!resp.ok) {
        const errText = await resp.text();
        console.error('[Auth] Token exchange failed:', resp.status, errText);
        return false;
      }

      const tokens = await resp.json();
      console.log('[Auth] Token exchange successful');

      _accessToken = tokens.access_token;
      _refreshToken = tokens.refresh_token;
      _idToken = tokens.id_token;

      // Parse the access token to get user info
      const parsed = _parseJwt(_accessToken);
      if (!parsed) return false;

      _demoMode = false;
      _currentUser = {
        sub: parsed.sub,
        email: parsed.email,
        name: parsed.name || parsed.preferred_username || parsed.email,
        roles: parsed.roles || parsed.realm_access?.roles || [],
        customer_id: parsed.customer_id || '0'
      };

      console.log('[Auth] SSO user authenticated:', _currentUser.email, 'roles:', _currentUser.roles);

      _saveSession();

      // Clean URL
      const cleanUrl = window.location.origin + window.location.pathname;
      window.history.replaceState({}, '', cleanUrl);

      // Start token refresh
      if (tokens.expires_in) {
        const refreshMs = Math.max((tokens.expires_in - 30) * 1000, 30000);
        setInterval(() => _refreshAccessToken(), refreshMs);
      }

      _notifyChange();
      return true;

    } catch(e) {
      console.error('[Auth] Token exchange error:', e);
      return false;
    }
  }

  async function _refreshAccessToken() {
    if (!_refreshToken) return;
    try {
      const resp = await fetch(TOKEN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'refresh_token',
          client_id: KC_CLIENT_ID,
          refresh_token: _refreshToken
        }).toString()
      });
      if (resp.ok) {
        const tokens = await resp.json();
        _accessToken = tokens.access_token;
        _refreshToken = tokens.refresh_token || _refreshToken;
        _saveSession();
      } else {
        console.warn('[Auth] Token refresh failed, logging out');
        logout();
      }
    } catch(e) {
      console.warn('[Auth] Token refresh error:', e);
    }
  }

  function keycloakLogout() {
    const logoutRedirect = window.location.origin + '/';
    const params = new URLSearchParams({
      client_id: KC_CLIENT_ID,
      post_logout_redirect_uri: logoutRedirect,
    });
    if (_idToken) {
      params.set('id_token_hint', _idToken);
    }
    _currentUser = null;
    _accessToken = null;
    _refreshToken = null;
    _idToken = null;
    localStorage.removeItem(_getStorageKey());
    window.location.href = LOGOUT_URL + '?' + params.toString();
  }

  // ---- Demo mode auth ----
  function demoLogin(role) {
    const user = DEMO_USERS[role];
    if (!user) return;
    _currentUser = { ...user };
    _demoMode = true;
    _saveSession();
    _notifyChange();
  }

  // ---- Common ----
  function logout() {
    if (!_demoMode && (_accessToken || _idToken)) {
      keycloakLogout();
      return;
    }
    _currentUser = null;
    _accessToken = null;
    _refreshToken = null;
    _idToken = null;
    localStorage.removeItem(_getStorageKey());
    _notifyChange();
  }

  function getUser() { return _currentUser; }
  function isAuthenticated() { return _currentUser !== null; }
  function hasRole(role) { return _currentUser?.roles?.includes(role) || false; }
  function isAdmin() { return hasRole('admin'); }
  function isEmployee() { return hasRole('employee'); }
  function isClient() { return hasRole('client'); }
  function isDemoMode() { return _demoMode; }

  function getToken() {
    if (!_demoMode && _accessToken) return _accessToken;
    return null;
  }

  function getAuthHeader() {
    const token = getToken();
    return token ? { 'Authorization': 'Bearer ' + token } : {};
  }

  function onAuthChange(callback) {
    _onAuthChange.push(callback);
    try { callback(_currentUser); } catch(e) { console.error('[Auth] Callback error:', e); }
  }

  function _notifyChange() {
    _onAuthChange.forEach(cb => {
      try { cb(_currentUser); } catch(e) { console.error('[Auth] Callback error:', e); }
    });
  }

  function _saveSession() {
    if (_currentUser) {
      localStorage.setItem(_getStorageKey(), JSON.stringify({
        user: _currentUser,
        demo: _demoMode,
        ts: Date.now()
      }));
    }
  }

  function _restoreSession() {
    try {
      const key = _getStorageKey();
      const raw = localStorage.getItem(key);
      if (!raw) return false;
      const data = JSON.parse(raw);
      if (Date.now() - data.ts > 8 * 60 * 60 * 1000) {
        localStorage.removeItem(key);
        return false;
      }
      // Validate role matches portal context
      const ctx = _getPortalContext();
      const roles = data.user?.roles || [];
      if (ctx === 'admin' && !roles.includes('admin')) { localStorage.removeItem(key); return false; }
      if (ctx === 'employee' && !roles.includes('employee')) { localStorage.removeItem(key); return false; }
      if (ctx === 'client' && !roles.includes('client')) { localStorage.removeItem(key); return false; }
      _currentUser = data.user;
      _demoMode = data.demo;
      return true;
    } catch(e) {
      return false;
    }
  }

  function renderUserBadge() {
    if (!_currentUser) return '';
    const roleColors = {
      admin: 'bg-red-500/20 text-red-400',
      employee: 'bg-cyan-500/20 text-cyan-400',
      client: 'bg-green-500/20 text-green-400'
    };
    const primaryRole = _currentUser.roles[0] || 'client';
    const roleClass = roleColors[primaryRole] || roleColors.client;
    const roleName = primaryRole.charAt(0).toUpperCase() + primaryRole.slice(1);
    const demoTag = _demoMode ? '<span class="text-amber-400 text-[10px] ml-1">(demo)</span>' : '';

    return `<div class="flex items-center gap-2">
      <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-dark-500/50">
        <div class="w-7 h-7 rounded-full bg-accent/20 flex items-center justify-center text-accent text-xs font-bold">
          ${_currentUser.name.charAt(0)}
        </div>
        <div class="text-xs">
          <div class="font-medium text-white">${_currentUser.name}${demoTag}</div>
          <span class="px-1.5 py-0.5 rounded text-[10px] font-medium ${roleClass}">${roleName}</span>
        </div>
      </div>
      <button onclick="window.BOAI_AUTH.logout()" class="p-1.5 rounded-lg hover:bg-red-500/10 text-dark-300 hover:text-red-400 transition-colors" title="Log out">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
        </svg>
      </button>
    </div>`;
  }

  function renderLoginScreen(options = {}) {
    const { title, subtitle, allowedRoles } = options;
    const t = window.BOAI_I18N?.t || (k => k);

    const ctx = _getPortalContext();
    const defaultRoles = ctx === 'client' ? ['client'] : ctx === 'admin' ? ['admin'] : ['employee'];
    const roles = allowedRoles || defaultRoles;
    const roleInfo = {
      admin: { icon: '\uD83D\uDEE1\uFE0F', color: 'red', desc: t('auth.role_admin') },
      employee: { icon: '\uD83D\uDCBC', color: 'cyan', desc: t('auth.role_employee') },
      client: { icon: '\uD83D\uDC64', color: 'green', desc: t('auth.role_client') }
    };

    const cards = roles.map(role => {
      const info = roleInfo[role];
      const user = DEMO_USERS[role];
      return `<button onclick="window.BOAI_AUTH.demoLogin('${role}')"
        class="glass-card rounded-xl p-5 text-left hover:border-${info.color}-500/40 transition-all group">
        <div class="text-3xl mb-3">${info.icon}</div>
        <div class="font-semibold text-white group-hover:text-${info.color}-400 transition-colors">${info.desc}</div>
        <div class="text-xs text-dark-300 mt-1">${user.email}</div>
      </button>`;
    }).join('');

    return `<div class="min-h-screen flex items-center justify-center p-4">
      <div class="max-w-lg w-full">
        <div class="text-center mb-8">
          <div class="w-16 h-16 mx-auto mb-4 rounded-2xl bg-accent/20 flex items-center justify-center">
            <svg class="w-8 h-8 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
            </svg>
          </div>
          <h2 class="text-2xl font-bold text-white mb-2" data-i18n="app.name">BankOffer AI</h2>
          <p class="text-dark-300" data-i18n="auth.demo_select">${t('auth.demo_select')}</p>
          <div class="mt-2 px-3 py-1 inline-block rounded-full bg-amber-500/10 text-amber-400 text-xs font-medium">
            Demo Mode
          </div>
        </div>
        <div class="grid gap-3">${cards}</div>
      </div>
    </div>`;
  }

  // ---- Initialize ----
  async function init() {
    // Handle cross-portal fresh session request (session isolation)
    if (window.location.search.includes('fresh=1')) {
      localStorage.removeItem(_getStorageKey());
      _currentUser = null;
      _demoMode = true;
      const url = new URL(window.location);
      url.searchParams.delete('fresh');
      window.history.replaceState({}, '', url.pathname + (url.search || ''));
    }

    // FIRST: check if this is an SSO callback with ?code= in URL
    const hasAuthCode = window.location.search.includes('code=');

    if (hasAuthCode) {
      console.log('[Auth] SSO callback detected in URL');
      // Clear any stale session before processing
      localStorage.removeItem(_getStorageKey());
      _currentUser = null;
      _demoMode = true;

      const ok = await _handleAuthCallback();
      if (ok) {
        console.log('[Auth] SSO login successful');
        return; // _notifyChange() already called in _handleAuthCallback
      }
      console.log('[Auth] SSO callback failed, falling through to login screen');
      _notifyChange();
      return;
    }

    // No auth callback — try restoring an existing session
    const restored = _restoreSession();
    if (restored) {
      _notifyChange();
      return;
    }

    // No session — show login screen
    _notifyChange();
  }

  // Expose API
  window.BOAI_AUTH = {
    init,
    getUser,
    isAuthenticated,
    hasRole,
    isAdmin,
    isEmployee,
    isClient,
    isDemoMode,
    isCustomerDomain: _isCustomerDomain,
    getToken,
    getAuthHeader,
    demoLogin,
    logout,
    onAuthChange,
    keycloakLogin,
    keycloakRegister,
    renderUserBadge,
    renderLoginScreen
  };

  // Auto-init immediately
  init();
})();
