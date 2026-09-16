"""Modern responsive workspace styling for Agentbox Console.

Linear/Raycast-inspired workspace design with left sidebar navigation,
domain concepts, adaptive dark mode, elevated cards, and responsive layout.
"""

STYLE = """
  :root {
    --bg: #f8fafc; --card-bg: #ffffff; --card-border: #e2e8f0;
    --card-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02);
    --text: #0f172a; --muted: #64748b; --accent: #2563eb; --accent-light: #eff6ff;
    --input-bg: #f8fafc; --input-border: #cbd5e1; --sidebar-bg: #f1f5f9;
    --sidebar-border: #e2e8f0; --sidebar-hover: #e2e8f0; --sidebar-active: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
  :root {
    --bg: #090d16; --card-bg: #0f172a; --card-border: #1e293b;
    --card-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); --text: #f8fafc; --muted: #94a3b8;
    --accent: #38bdf8; --accent-light: rgba(56,189,248,0.1); --input-bg: #0b1120;
    --input-border: #334155; --sidebar-bg: #0b1120; --sidebar-border: #1e293b;
    --sidebar-hover: #1e293b; --sidebar-active: #1e293b;
  }
  }
  * { box-sizing: border-box; }
  body {

    font: 14.5px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
    margin: 0; color: var(--text); background: var(--bg);
    -webkit-font-smoothing: antialiased;
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
    display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0.5rem 0.85rem;
    border-bottom: 1px solid var(--sidebar-border); margin-bottom: 0.85rem;
  }
  .brand-title {
    font-size: 1.05rem; font-weight: 700; letter-spacing: -0.02em;
  }
  .brand-title a {
    text-decoration: none; color: inherit;
  }
  .env-badge {
    font-size: 0.65rem; font-weight: 600; text-transform: uppercase;
    background: var(--card-border); color: var(--muted); border-radius: 4px;
    padding: 0.15rem 0.35rem; margin-left: auto;
  }
  .sidebar-section {
    margin-bottom: 1.1rem;
  }
  .section-label {
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--muted); padding: 0 0.5rem 0.35rem;
  }
  .nav-menu {
    display: flex; flex-direction: column; gap: 0.15rem;
  }
  .nav-item {
    display: flex; align-items: center; gap: 0.55rem; padding: 0.48rem 0.65rem;
    border-radius: 7px; text-decoration: none; color: var(--muted); font-size: 0.88rem;
    font-weight: 500; transition: all 0.15s ease;
  }
  .nav-item:hover {
    background: var(--sidebar-hover); color: var(--text);
  }
  .nav-item.on {
    background: var(--sidebar-active); color: var(--accent); font-weight: 600;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  }
  .nav-badge {
    margin-left: auto; background: var(--accent); color: #ffffff; font-size: 0.68rem;
    font-weight: 700; border-radius: 9999px; padding: 0.1rem 0.45rem; line-height: 1.2;
  }
  .sidebar-footer {
    margin-top: auto; padding-top: 0.75rem; border-top: 1px solid var(--sidebar-border);
    display: flex; flex-direction: column; gap: 0.75rem;
  }
  .app-switch-box {
    display: flex; flex-direction: column; gap: 0.3rem;
  }
  .app-switch-label {
    font-size: 0.68rem; font-weight: 600; text-transform: uppercase; color: var(--muted);
    padding: 0 0.2rem;
  }
  .app-switch-row {
    display: flex; gap: 0.3rem; flex-wrap: wrap;
  }
  .app-switch-row a {
    font-size: 0.72rem; font-weight: 500; color: var(--muted); text-decoration: none;
    padding: 0.2rem 0.45rem; border-radius: 5px; background: var(--card-bg);
    border: 1px solid var(--card-border); transition: all 0.15s ease;
  }
  .app-switch-row a:hover {
    background: var(--card-border); color: var(--text);
  }
  .user-profile-row {
    display: flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0.2rem 0;
  }
  .avatar-circle {
    width: 1.8rem; height: 1.8rem; border-radius: 50%; background: var(--accent);
    color: white; display: flex; align-items: center; justify-content: center;
    font-size: 0.8rem; font-weight: 700;
  }
  .user-details {
    flex: 1; min-width: 0; display: flex; flex-direction: column;
  }
  .user-name-text {
    font-size: 0.82rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap;
  }
  .user-role-tag {
    font-size: 0.65rem; color: var(--muted);
  }
  .signout-link {
    font-size: 0.8rem; color: var(--muted); text-decoration: none;
    padding: 0.2rem 0.4rem; border-radius: 4px;
  }
  .signout-link:hover {
    color: var(--text); background: var(--sidebar-hover);
  }
  .main-content {
    flex: 1; min-width: 0; padding: 1.75rem 2.25rem 4rem; max-width: 58rem;
    margin: 0 auto;
  }
  .shell {
    max-width: 56rem; margin: 0 auto; padding: 1.5rem 1.25rem 3.5rem;
  }
  h1 {
    font-size: 1.35rem; font-weight: 700; margin: 0 0 0.3rem; letter-spacing: -0.02em;
  }
  h2 {
    font-size: 1.05rem; font-weight: 650; margin: 1.75rem 0 0.4rem;
    letter-spacing: -0.01em;
  }
  .sub {
    color: var(--muted); font-size: 0.88rem; margin: 0 0 0.75rem;
  }
  .card {
    background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: 10px; padding: 1.2rem 1.35rem; margin: 0 0 1rem;
    box-shadow: var(--card-shadow);
  }
  .card.warn {
    border-color: #f59e0b; background: rgba(245,158,11,0.04);
  }
  .row {
    display: flex; align-items: baseline; gap: 0.6rem; padding: 0.35rem 0;
    border-bottom: 1px solid rgba(0,0,0,0.04);
  }
  .row:last-child { border-bottom: none; }
  .row .name { flex: 1; font-size: 0.88rem; }
  .when { color: var(--muted); font-size: 0.8rem; }
  .dot {
    width: 0.55rem; height: 0.55rem; border-radius: 50%; flex: none;
    display: inline-block; position: relative; top: -0.08rem;
  }
  .dot.ok { background: #16a34a; box-shadow: 0 0 0 2px rgba(22,163,74,0.15); }
  .dot.warn { background: #f59e0b; box-shadow: 0 0 0 2px rgba(245,158,11,0.15); }
  .dot.fail { background: #ef4444; box-shadow: 0 0 0 2px rgba(239,68,68,0.15); }
  .scope {
    display: inline-flex; align-items: center; font-size: 0.7rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.04em; background: #e0e7ff;
    color: #3730a3; border-radius: 9999px; padding: 0.15rem 0.55rem;
    margin-bottom: 0.55rem;
  }
  @media (prefers-color-scheme: dark) {
  .scope { background: #1e1b4b; color: #a5b4fc; }
  }
  .badge {
    display: inline-block; font-size: 0.65rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.04em; background: #e2e8f0;
    color: #334155; border-radius: 9999px; padding: 0.15rem 0.5rem; margin-left: 0.4rem;
  }
  @media (prefers-color-scheme: dark) {
  .badge { background: #1e293b; color: #94a3b8; }
  }
  .stmt { margin: 0 0 0.85rem; font-size: 0.95rem; }
  textarea.statement-box {
    width: 100%; border-radius: 8px; border: 1px solid var(--input-border);
    background: var(--input-bg); color: var(--text); padding: 0.75rem;
    font-family: inherit; font-size: 0.925rem; line-height: 1.55; resize: vertical;
    min-height: 4.8rem; margin-bottom: 0.6rem;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }
  textarea.statement-box:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(37,99,235,0.12);
  }
  button {
    display: inline-flex; align-items: center; justify-content: center; font: inherit;
    font-size: 0.85rem; font-weight: 500; padding: 0.42rem 0.9rem; border-radius: 6px;
    cursor: pointer; border: 1px solid var(--card-border); background: var(--card-bg);
    color: var(--text); margin-right: 0.45rem; margin-bottom: 0.35rem;
    transition: all 0.15s ease;
  }
  button:hover {
    background: var(--input-bg); border-color: var(--input-border);
  }
  button.yes {
    background: #15803d; border-color: #15803d; color: #ffffff;
    box-shadow: 0 1px 2px rgba(21,128,61,0.2);
  }
  button.yes:hover {
    background: #166534; border-color: #166534;
  }
  button.danger {
    color: #dc2626; border-color: #fca5a5; background: var(--card-bg);
  }
  button.danger:hover {
    background: #dc2626; border-color: #dc2626; color: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
  button.danger {
    color: #f87171; border-color: #991b1b;
  }
  button.danger:hover {
    background: #ef4444; border-color: #ef4444; color: #ffffff;
  }
  }
  .flash {
    background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3);
    color: #15803d; padding: 0.65rem 0.95rem; border-radius: 8px; margin: 0 0 1rem;
    font-size: 0.88rem;
  }
  @media (prefers-color-scheme: dark) {
  .flash { color: #4ade80; }
  }
  .empty {
    color: var(--muted); background: var(--card-bg);
    border: 1px dashed var(--card-border); border-radius: 10px; padding: 2rem 1.25rem;
    text-align: center; font-size: 0.9rem;
  }
  .search-bar {
    width: 100%; padding: 0.6rem 0.85rem; border-radius: 8px;
    border: 1px solid var(--input-border); background: var(--card-bg);
    color: var(--text); font-size: 0.9rem; margin-bottom: 1rem;
  }
  .search-bar:focus {
    outline: none; border-color: var(--accent);
  }
  .filter-group {
    display: flex; gap: 0.4rem; margin-bottom: 1.25rem;
  }
  .filter-btn {
    font-size: 0.78rem; padding: 0.25rem 0.65rem; border-radius: 9999px;
    border: 1px solid var(--card-border); background: var(--card-bg);
    color: var(--muted); cursor: pointer;
  }
  .filter-btn.active {
    background: var(--accent); color: white; border-color: var(--accent);
  }
  footer {
    margin-top: 2.5rem; color: var(--muted); font-size: 0.82rem;
  }
  @media (max-width: 768px) {
  .app-layout { flex-direction: column; }
  .sidebar {
    width: 100%; height: auto; position: static; border-right: none;
    border-bottom: 1px solid var(--sidebar-border); padding: 0.75rem 1rem;
  }
  .brand-row { margin-bottom: 0.5rem; padding-bottom: 0.5rem; }
  .sidebar-section .section-label { display: none; }
  .nav-menu { flex-direction: row; overflow-x: auto; gap: 0.4rem; }
  .sidebar-footer { display: none; }
  .main-content { padding: 1.25rem 1rem 3rem; }
  }
"""
