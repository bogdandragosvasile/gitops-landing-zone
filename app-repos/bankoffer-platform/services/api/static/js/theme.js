/**
 * BankOffer — Theme Switcher (Light / Dark)
 * Toggles data-theme="dark" on <html> to activate CSS variable overrides.
 */
(function() {
  const STORAGE_KEY = 'boai_theme';

  function getTheme() {
    return localStorage.getItem(STORAGE_KEY) || 'light';
  }

  function applyTheme(theme) {
    if (theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    localStorage.setItem(STORAGE_KEY, theme);
    // Sync all rendered toggle buttons
    document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
      const label = btn.querySelector('.theme-label');
      if (label) label.textContent = theme === 'dark' ? 'Light' : 'Dark';
    });
  }

  function toggleTheme() {
    applyTheme(getTheme() === 'dark' ? 'light' : 'dark');
  }

  function renderToggle() {
    const theme = getTheme();
    return `<button data-theme-toggle onclick="window.BOAI_THEME.toggle()"
      class="text-xs font-medium px-2.5 py-1.5 rounded border transition-colors"
      style="border-color: var(--border); color: var(--text-secondary); background: transparent;"
      title="Toggle theme">
      <span class="theme-label">${theme === 'dark' ? 'Light' : 'Dark'}</span>
    </button>`;
  }

  // Apply immediately on load
  applyTheme(getTheme());

  window.BOAI_THEME = {
    get: getTheme,
    set: applyTheme,
    toggle: toggleTheme,
    renderToggle: renderToggle
  };
})();
