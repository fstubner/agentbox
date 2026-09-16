"""Modern professional workspace styling for Agentbox Console.

Linear/Raycast aesthetic with monochrome SVG icons, slate-charcoal dark mode,
crisp borders, refined button hierarchy, and responsive layout.
"""

STYLE = """
  :root {
    --bg: #f8fafc; --card-bg: #ffffff; --card-border: #e2e8f0;
    --card-shadow: 0 1px 3px rgba(0,0,0,0.03), 0 1px 2px rgba(0,0,0,0.02);
    --text: #0f172a; --muted: #64748b; --accent: #3b82f6;
    --accent-soft: rgba(59,130,246,0.08); --input-bg: #f8fafc; --input-border: #cbd5e1;
    --sidebar-bg: #f1f5f9; --sidebar-border: #e2e8f0; --sidebar-hover: #e2e8f0;
    --sidebar-active: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
  :root {
    --bg: #0c0e12; --card-bg: #13161c; --card-border: #1f242f;
    --card-shadow: 0 4px 12px rgba(0,0,0,0.4); --text: #f1f3f7; --muted: #828a9b;
    --accent: #4f6bff; --accent-soft: rgba(79,107,255,0.12); --input-bg: #0e1015;
    --input-border: #262c3a; --sidebar-bg: #101217; --sidebar-border: #1a1e27;
    --sidebar-hover: #181b23; --sidebar-active: #1d212b;
  }
  }
  * { box-sizing: border-box; }
  body {

    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
    margin: 0; color: var(--text); background: var(--bg);
    -webkit-font-smoothing: antialiased;
  }
  a {
    color: var(--accent); text-decoration: none; transition: color 0.12s ease;
  }
  a:hover {
    text-decoration: underline; color: #7088ff;
  }
  .app-layout {
    display: flex; min-height: 100vh;
  }
  .sidebar {
    width: 15rem; flex-shrink: 0; background: var(--sidebar-bg);
    border-right: 1px solid var(--sidebar-border); display: flex; flex-direction: column;
    padding: 1rem 0.75rem; position: sticky; top: 0; height: 100vh; overflow-y: auto;
  }
  .brand-row {
    display: flex; align-items: center; gap: 0.6rem; padding: 0.25rem 0.4rem 0.85rem;
    border-bottom: 1px solid var(--sidebar-border); margin-bottom: 1rem;
  }
  .brand-title {
    font-size: 0.95rem; font-weight: 700; letter-spacing: -0.01em;
  }
  .brand-title a {
    text-decoration: none; color: inherit;
  }
  .env-badge {
    font-size: 0.62rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.04em; background: var(--card-border); color: var(--muted);
    border-radius: 4px; padding: 0.15rem 0.35rem; margin-left: auto;
  }
  .sidebar-section {
    margin-bottom: 1.25rem;
  }
  .section-label {
    font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--muted); padding: 0 0.5rem 0.4rem; opacity: 0.8;
  }
  .nav-menu {
    display: flex; flex-direction: column; gap: 0.15rem;
  }
  .nav-item {
    display: flex; align-items: center; gap: 0.65rem; padding: 0.45rem 0.6rem;
    border-radius: 6px; text-decoration: none; color: var(--muted); font-size: 0.86rem;
    font-weight: 500; transition: all 0.12s ease;
  }
  .nav-item svg {
    color: var(--muted); flex-shrink: 0; transition: color 0.12s ease;
  }
  .nav-item:hover {
    background: var(--sidebar-hover); color: var(--text);
  }
  .nav-item:hover svg {
    color: var(--text);
  }
  .nav-item.on {
    background: var(--sidebar-active); color: var(--text); font-weight: 600;
    box-shadow: 0 1px 2px rgba(0,0,0,0.1);
  }
  .nav-item.on svg {
    color: var(--accent);
  }
  .nav-badge {
    margin-left: auto; background: var(--accent); color: #ffffff; font-size: 0.68rem;
    font-weight: 700; border-radius: 9999px; padding: 0.08rem 0.45rem; line-height: 1.2;
  }
  .sidebar-footer {
    margin-top: auto; padding-top: 0.85rem; border-top: 1px solid var(--sidebar-border);
    display: flex; flex-direction: column; gap: 0.85rem;
  }
  input:not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"]):not([type="hidden"]),
  select, textarea {
    background: var(--input-bg) !important;
    color: var(--text) !important;
    border: 1px solid var(--input-border) !important;
    border-radius: 6px;
    font-family: inherit;
    font-size: 0.88rem;
  }
  input:not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"]):not([type="hidden"]):focus,
  select:focus, textarea:focus {
    outline: none;
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px var(--accent-soft);
  }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.85em; background: var(--input-bg); color: var(--text);
    padding: 0.15rem 0.35rem; border-radius: 4px; border: 1px solid var(--card-border);
  }
  input[type="checkbox"] {
    accent-color: var(--accent);
  }
  .user-profile-row {
    display: flex; align-items: center; gap: 0.55rem; padding: 0.35rem 0.2rem 0;
  }
  .avatar-circle {
    width: 1.75rem; height: 1.75rem; border-radius: 50%; background: var(--accent);
    color: white; display: flex; align-items: center; justify-content: center;
    font-size: 0.78rem; font-weight: 700; flex-shrink: 0;
  }
  .user-details {
    flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.1rem;
  }
  .user-name-text {
    font-size: 0.82rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap;
  }
  .user-role-tag {
    font-size: 0.65rem; color: var(--muted); display: flex; align-items: center;
    gap: 0.3rem;
  }
  .signout-link {
    color: var(--muted); display: flex; align-items: center; justify-content: center;
    padding: 0.25rem; border-radius: 4px; transition: all 0.12s ease;
  }
  .signout-link:hover {
    color: var(--text); background: var(--sidebar-hover);
  }
  .main-content {
    flex: 1; min-width: 0; padding: 2rem 2.5rem 4rem; max-width: 58rem; margin: 0 auto;
  }
  .shell {
    max-width: 56rem; margin: 0 auto; padding: 1.5rem 1.25rem 3.5rem;
  }
  h1 {
    font-size: 1.35rem; font-weight: 700; margin: 0 0 0.35rem; letter-spacing: -0.02em;
  }
  h2 {
    font-size: 1.05rem; font-weight: 650; margin: 2rem 0 0.5rem; letter-spacing: -0.01em;
  }
  .sub {
    color: var(--muted); font-size: 0.88rem; margin: 0 0 0.85rem;
  }
  .card {
    background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 8px;
    padding: 1.15rem 1.35rem; margin: 0 0 1rem; box-shadow: var(--card-shadow);
  }
  .card.warn {
    border-color: #d97706; background: rgba(217,119,6,0.05);
  }
  .row {
    display: flex; align-items: center; gap: 0.65rem; padding: 0.45rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.03);
  }
  .row:last-child { border-bottom: none; }
  .row .name { flex: 1; font-size: 0.88rem; }
  .when a {
    font-size: 0.76rem; font-weight: 500; color: var(--muted); text-decoration: none;
    padding: 0.15rem 0.5rem; border-radius: 4px; background: var(--input-bg);
    border: 1px solid var(--card-border); transition: all 0.12s ease;
  }
  .when a:hover {
    color: var(--text); border-color: var(--input-border);
  }
  .dot {
    width: 0.5rem; height: 0.5rem; border-radius: 50%; flex: none; display: inline-block;
  }
  .dot.ok { background: #22c55e; }
  .dot.warn { background: #f59e0b; }
  .dot.fail { background: #ef4444; }
  .scope {
    display: inline-flex; align-items: center; font-size: 0.65rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em;
    background: rgba(255,255,255,0.06); color: var(--muted);
    border: 1px solid rgba(255,255,255,0.08); border-radius: 4px;
    padding: 0.15rem 0.45rem; margin-bottom: 0.55rem;
  }
  .badge {
    display: inline-block; font-size: 0.62rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.04em; background: var(--input-bg);
    color: var(--muted); border: 1px solid var(--card-border); border-radius: 4px;
    padding: 0.12rem 0.45rem;
  }
  .stmt {
    margin: 0 0 0.85rem; font-size: 0.92rem; line-height: 1.55;
  }
  textarea.statement-box {
    width: 100%; border-radius: 6px; border: 1px solid var(--input-border);
    background: var(--input-bg); color: var(--text); padding: 0.7rem;
    font-family: inherit; font-size: 0.88rem; line-height: 1.55; resize: vertical;
    min-height: 4.8rem; margin-bottom: 0.6rem; scrollbar-width: thin;
    transition: border-color 0.15s ease;
  }
  textarea.statement-box:focus {
    outline: none; border-color: var(--accent);
  }
  button, .button, a.button {
    display: inline-flex; align-items: center; justify-content: center; font: inherit;
    font-size: 0.82rem; font-weight: 500; padding: 0.42rem 0.85rem; border-radius: 6px;
    cursor: pointer; border: 1px solid var(--card-border); background: var(--card-bg);
    color: var(--text) !important; margin-right: 0.45rem; margin-bottom: 0.35rem;
    text-decoration: none !important; transition: all 0.12s ease; line-height: 1.4;
  }
  button:hover, .button:hover, a.button:hover {
    background: var(--sidebar-hover); border-color: var(--input-border);
    color: var(--text) !important; text-decoration: none !important;
  }
  button.yes, .button.yes, a.button.yes {
    background: #2563eb !important; border-color: #3b82f6 !important; color: #ffffff !important;
  }
  button.yes:hover, .button.yes:hover, a.button.yes:hover {
    background: #1d4ed8 !important; border-color: #2563eb !important; color: #ffffff !important;
  }
  button.danger, .button.danger, a.button.danger {
    color: #f87171 !important; border-color: rgba(239,68,68,0.25) !important; background: transparent !important;
  }
  button.danger:hover, .button.danger:hover, a.button.danger:hover {
    background: rgba(239,68,68,0.1) !important; border-color: #ef4444 !important; color: #fca5a5 !important;
  }
  .flash {
    background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.25);
    color: #22c55e; padding: 0.65rem 0.95rem; border-radius: 6px; margin: 0 0 1rem;
    font-size: 0.86rem;
  }
  .empty {
    color: var(--muted); background: var(--card-bg);
    border: 1px dashed var(--card-border); border-radius: 8px; padding: 2rem 1.25rem;
    text-align: center; font-size: 0.88rem;
  }
  .search-bar {
    width: 100%; padding: 0.55rem 0.8rem; border-radius: 6px;
    border: 1px solid var(--input-border); background: var(--card-bg);
    color: var(--text); font-size: 0.88rem; margin-bottom: 0.85rem;
  }
  .search-bar:focus {
    outline: none; border-color: var(--accent);
  }
  .filter-group {
    display: flex; gap: 0.35rem; margin-bottom: 1.25rem;
  }
  .filter-btn {
    font-size: 0.75rem; padding: 0.25rem 0.65rem; border-radius: 4px;
    border: 1px solid var(--card-border); background: var(--card-bg);
    color: var(--muted); cursor: pointer;
  }
  .filter-btn.active {
    background: var(--accent); color: white; border-color: var(--accent);
  }
  footer {
    margin-top: 2.5rem; color: var(--muted); font-size: 0.8rem;
  }
  @media (max-width: 768px) {
  .app-layout { flex-direction: column; }
  .sidebar {
    width: 100%; height: auto; position: static; border-right: none;
    border-bottom: 1px solid var(--sidebar-border); padding: 0.75rem 1rem;
  }
  .brand-row { margin-bottom: 0.5rem; padding-bottom: 0.5rem; }
  .sidebar-section .section-label { display: none; }
  .nav-menu { flex-direction: row; overflow-x: auto; gap: 0.35rem; }
  .sidebar-footer { display: none; }
  .main-content { padding: 1.25rem 1rem 3rem; }
  }
  .task-check {
    width: 19px; height: 19px; border-radius: 4px;
    border: 1.5px solid var(--border); background: var(--input-bg);
    cursor: pointer; display: inline-flex; align-items: center;
    justify-content: center; padding: 0; font-size: 0.72rem;
    color: transparent; transition: all 0.15s ease;
  }
  .task-check:hover {
    border-color: var(--accent); background: rgba(99, 102, 241, 0.15);
    color: var(--accent);
  }
  .task-check.done {
    background: var(--accent); border-color: var(--accent); color: white;
  }
  .form-group { display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 0.85rem; }
  .field-input { width: 100%; padding: 0.6rem 0.8rem; font-size: 0.92rem; border-radius: 6px; }
  .item-row { display: flex; align-items: center; gap: 0.6rem; }
  .sub-zero { margin: 0; }
  .inline-input { padding: 0.4rem 0.5rem; margin: 0 0.4rem 0.4rem 0; font-size: 0.9rem; border-radius: 4px; }
  .full-input { display: block; width: 100%; margin: 0.35rem 0 0.2rem; }
  .summary-hint { cursor: pointer; font-size: 0.8rem; color: var(--muted); }
  .divider-top { margin: 0.8rem 0 0; padding-top: 0.7rem; border-top: 1px solid var(--border); }
"""

ICON_BRAND = (
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" '
    'stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" '
    'stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
)
ICON_INBOX = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/>'
    '<path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6'
    'l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>'
)
ICON_KNOWLEDGE = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'
    '<path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>'
)
ICON_CAPABILITIES = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><rect x="2" y="2" width="8" height="8" rx="2"/>'
    '<rect x="14" y="2" width="8" height="8" rx="2"/>'
    '<rect x="2" y="14" width="8" height="8" rx="2"/>'
    '<rect x="14" y="14" width="8" height="8" rx="2"/></svg>'
)
ICON_OPERATIONS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><circle cx="12" cy="12" r="3"/>'
    '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83'
    'l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0'
    'v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83'
    'l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4'
    'h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83'
    'l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0'
    'v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83'
    'l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4'
    'h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
)
ICON_LOGOUT = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
    '<polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>'
)

ICON_TASKS = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"><path d="M9 11l3 3L22 4"/>'
    '<path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>'
)
