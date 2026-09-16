"""Modern responsive styling for Agentbox Management Console.

Self-contained stylesheet with semantic CSS variables, adaptive dark mode,
elevated card surfaces, modern button hierarchy, and responsive layout.
"""

STYLE = """
:root {
  --bg: #f8fafc;
  --card-bg: #ffffff;
  --card-border: #e2e8f0;
  --card-shadow: 0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.03);
  --text: #0f172a;
  --muted: #64748b;
  --accent: #2563eb;
  --input-bg: #f8fafc;
  --input-border: #cbd5e1;
  --topbar-bg: rgba(255,255,255,0.92);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #090d16;
    --card-bg: #0f172a;
    --card-border: #1e293b;
    --card-shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
    --text: #f8fafc;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --input-bg: #0b1120;
    --input-border: #334155;
    --topbar-bg: rgba(15,23,42,0.92);
  }
}

* { box-sizing: border-box; }
body {
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
  margin: 0;
  color: var(--text);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
}

header.top {
  position: sticky;
  top: 0;
  z-index: 50;
  width: 100%;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  background: var(--topbar-bg);
  border-bottom: 1px solid var(--card-border);
}

header.top .top-inner {
  max-width: 60rem;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 1.25rem;
}

.brand {
  font-size: 1.05rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.who {
  margin-left: auto;
  color: var(--muted);
  font-size: 0.85rem;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
}

.badge {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: #e2e8f0;
  color: #334155;
  border-radius: 9999px;
  padding: 0.15rem 0.5rem;
  margin-left: 0.4rem;
}

@media (prefers-color-scheme: dark) {
  .badge { background: #1e293b; color: #94a3b8; }
}

.app-switch {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-left: 0.8rem;
}

.app-switch a {
  font-size: 0.78rem;
  font-weight: 500;
  color: var(--muted);
  text-decoration: none;
  padding: 0.2rem 0.55rem;
  border-radius: 6px;
  background: var(--input-bg);
  border: 1px solid var(--card-border);
  transition: all 0.15s ease;
}

.app-switch a:hover {
  background: var(--card-border);
  color: var(--text);
}

.shell {
  max-width: 60rem;
  margin: 0 auto;
  padding: 0 1.25rem 3.5rem;
}

.tabs {
  display: flex;
  gap: 0.4rem;
  border-bottom: 1px solid var(--card-border);
  margin: 1.25rem 0 1.5rem;
  overflow-x: auto;
  white-space: nowrap;
}

.tabs a {
  padding: 0.6rem 0.95rem;
  text-decoration: none;
  color: var(--muted);
  font-size: 0.92rem;
  font-weight: 500;
  border-bottom: 2px solid transparent;
  border-radius: 6px 6px 0 0;
  transition: all 0.15s ease;
}

.tabs a:hover {
  color: var(--text);
  background: rgba(0,0,0,0.02);
}

.tabs a.on {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}

h1 {
  font-size: 1.35rem;
  font-weight: 700;
  margin: 0 0 0.3rem;
  letter-spacing: -0.02em;
}

h2 {
  font-size: 1.1rem;
  font-weight: 650;
  margin: 1.75rem 0 0.35rem;
  letter-spacing: -0.01em;
}

.sub {
  color: var(--muted);
  font-size: 0.88rem;
  margin: 0 0 0.75rem;
}

.card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 1.25rem 1.4rem;
  margin: 0 0 1rem;
  box-shadow: var(--card-shadow);
}

.card.warn {
  border-color: #f59e0b;
  background: rgba(245,158,11,0.05);
}

.row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.35rem 0;
  border-bottom: 1px solid rgba(0,0,0,0.04);
}

.row:last-child { border-bottom: none; }
.row .name { flex: 1; font-size: 0.9rem; }
.when { color: var(--muted); font-size: 0.82rem; }

.dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  flex: none;
  display: inline-block;
  position: relative;
  top: -0.08rem;
}

.dot.ok { background: #16a34a; box-shadow: 0 0 0 2px rgba(22,163,74,0.15); }
.dot.warn { background: #f59e0b; box-shadow: 0 0 0 2px rgba(245,158,11,0.15); }
.dot.fail { background: #ef4444; box-shadow: 0 0 0 2px rgba(239,68,68,0.15); }

.scope {
  display: inline-flex;
  align-items: center;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  background: #e0e7ff;
  color: #3730a3;
  border-radius: 9999px;
  padding: 0.15rem 0.55rem;
  margin-bottom: 0.6rem;
}

@media (prefers-color-scheme: dark) {
  .scope { background: #1e1b4b; color: #a5b4fc; }
}

.stmt { margin: 0 0 0.85rem; font-size: 0.95rem; }

textarea.statement-box {
  width: 100%;
  border-radius: 8px;
  border: 1px solid var(--input-border);
  background: var(--input-bg);
  color: var(--text);
  padding: 0.75rem;
  font-family: inherit;
  font-size: 0.925rem;
  line-height: 1.55;
  resize: vertical;
  min-height: 5rem;
  margin-bottom: 0.6rem;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

textarea.statement-box:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(37,99,235,0.12);
}

button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font: inherit;
  font-size: 0.875rem;
  font-weight: 500;
  padding: 0.45rem 0.95rem;
  border-radius: 7px;
  cursor: pointer;
  border: 1px solid var(--card-border);
  background: var(--card-bg);
  color: var(--text);
  margin-right: 0.45rem;
  margin-bottom: 0.35rem;
  transition: all 0.15s ease;
}

button:hover {
  background: var(--input-bg);
  border-color: var(--input-border);
}

button.yes {
  background: #15803d;
  border-color: #15803d;
  color: #ffffff;
  box-shadow: 0 1px 2px rgba(21,128,61,0.2);
}

button.yes:hover {
  background: #166534;
  border-color: #166534;
}

button.danger {
  color: #dc2626;
  border-color: #fca5a5;
  background: var(--card-bg);
}

button.danger:hover {
  background: #dc2626;
  border-color: #dc2626;
  color: #ffffff;
}

@media (prefers-color-scheme: dark) {
  button.danger {
    color: #f87171;
    border-color: #991b1b;
  }
  button.danger:hover {
    background: #ef4444;
    border-color: #ef4444;
    color: #ffffff;
  }
}

.flash {
  background: rgba(34,197,94,0.1);
  border: 1px solid rgba(34,197,94,0.3);
  color: #15803d;
  padding: 0.65rem 0.95rem;
  border-radius: 8px;
  margin: 0 0 1rem;
  font-size: 0.9rem;
}

@media (prefers-color-scheme: dark) {
  .flash { color: #4ade80; }
}

.empty {
  color: var(--muted);
  background: var(--card-bg);
  border: 1px dashed var(--card-border);
  border-radius: 12px;
  padding: 2rem 1.25rem;
  text-align: center;
  font-size: 0.925rem;
}

footer {
  margin-top: 2.5rem;
  color: var(--muted);
  font-size: 0.85rem;
}
"""
