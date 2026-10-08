// Provider labels are text; icons come only from this local, trusted set.
const ICONS = {
  shield: '<path d="M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7Z"/><path d="m8 12 3 3 5-6"/>',
  key: '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-4-4 3-3m-6 0 3-3"/>',
  building: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h1m4 0h1m-6 4h1m4 0h1m-5 10v-6h4v6"/>',
};

export function renderSso(config, passwordEnabled, doc = document, win = window) {
  const section = doc.getElementById('oidcSection');
  const button = doc.getElementById('oidcBtn');
  const label = doc.getElementById('oidcLabel');
  const status = doc.getElementById('signInStatus');
  const retry = doc.getElementById('signInRetry');
  status.textContent = '';
  if (!config?.enabled) {
    section.style.display = 'none';
    status.textContent = passwordEnabled
      ? 'Single sign-on is temporarily unavailable. You can use your password or try again.'
      : 'Single sign-on is unavailable. Try again shortly or contact your administrator.';
    retry.style.display = '';
    return;
  }
  const provider = config.provider_name || 'SSO';
  const normalLabel = 'Sign in with ' + provider;
  label.textContent = normalLabel;
  doc.getElementById('oidcIcon').innerHTML = Object.hasOwn(ICONS, config.provider_icon) ? ICONS[config.provider_icon] : ICONS.shield;
  doc.getElementById('oidcDivider').style.display = passwordEnabled ? '' : 'none';
  section.style.display = 'block';
  retry.style.display = 'none';
  const reset = () => {
    button.removeAttribute('aria-disabled');
    button.removeAttribute('aria-busy');
    label.textContent = normalLabel;
    status.textContent = '';
  };
  button.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
    if (button.getAttribute('aria-disabled') === 'true') {
      event.preventDefault();
      return;
    }
    button.setAttribute('aria-disabled', 'true');
    button.setAttribute('aria-busy', 'true');
    label.textContent = 'Connecting to ' + provider + '…';
    status.textContent = 'Continue signing in with your identity provider.';
  });
  win.addEventListener('pageshow', reset);
  if (!passwordEnabled) button.focus();
}
