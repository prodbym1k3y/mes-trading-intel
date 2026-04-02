"""AGGRESSIVE 80s neon retro theme — Tron / Blade Runner / synthwave aesthetic.

Deep black with hot neon cyan primary, electric magenta secondary,
neon green profits, hot pink losses. Everything glows. Everything is monospace.
This is what a hacker trading terminal from 1985 looks like.
"""

# Color palette — pure neon aggression
COLORS = {
    # Backgrounds — deep black, slight blue tint
    "bg_dark":   "#050508",
    "bg_panel":  "#080810",
    "bg_card":   "#0a0a14",
    "bg_input":  "#06060e",
    "bg_hover":  "#0f0f20",

    # PRIMARY: hot neon cyan (Tron lines)
    "cyan":          "#00ffff",
    "cyan_mid":      "#00cccc",
    "cyan_dim":      "#005566",
    "cyan_glow":     "#00ffff30",

    # SECONDARY: electric magenta/pink (Blade Runner neon)
    "magenta":       "#ff00ff",
    "magenta_mid":   "#cc00cc",
    "magenta_dim":   "#550055",

    # ACCENT: neon green profits
    "green_bright":  "#00ff41",
    "green_mid":     "#00cc33",
    "green_dim":     "#004411",

    # LOSS: hot pink
    "pink":          "#ff0066",
    "pink_mid":      "#cc0055",
    "pink_dim":      "#550022",

    # WARNING: neon orange
    "orange":        "#ff6600",
    "orange_dim":    "#663300",

    # TEXT
    "text_primary":  "#00ffff",
    "text_secondary":"#ff00ff",
    "text_muted":    "#224444",
    "text_white":    "#ccdddd",

    # Signals
    "long_color":    "#00ff41",
    "short_color":   "#ff0066",
    "flat_color":    "#ff6600",

    # Delta
    "delta_positive":"#00ff41",
    "delta_negative":"#ff0066",
    "delta_neutral": "#334444",

    # Borders — glowing cyan
    "border":        "#003344",
    "border_bright": "#00ffff88",
    "grid":          "#0a0a1a",

    # Extra
    "amber":         "#ffcc00",
    "amber_dim":     "#554400",
    "red":           "#ff0066",
    "red_dim":       "#550022",
    "blue":          "#0066ff",
    "neon_pink":     "#ff0080",
    "neon_yellow":   "#ffff00",
    "neon_orange":   "#ff6600",
    "hologram":      "#80ffff",
    "deep_purple":   "#100020",

    # ── Backwards-compat aliases (referenced by widgets/other modules) ──
    "green_glow":    "#00ff4130",   # transparent green glow
    "text_secondary":"#ff00ff",     # magenta (secondary text)
    "border_bright": "#00ffff55",   # glowing cyan border
}


STYLESHEET = f"""
/* ═══════════════════════════════════════════════════════════════
   MES INTEL — 80s NEON TRADING TERMINAL
   Tron / Blade Runner / Synthwave aesthetic
   ═══════════════════════════════════════════════════════════════ */

QMainWindow {{
    background-color: {COLORS['bg_dark']};
    border: 2px solid {COLORS['cyan_dim']};
}}

QWidget {{
    background-color: transparent;
    color: {COLORS['text_primary']};
    font-family: 'Courier New', 'Monaco', 'Menlo', 'Consolas', monospace;
    font-size: 12px;
}}

/* ── Main central widget ── */
QWidget#central {{
    background-color: {COLORS['bg_dark']};
}}

/* ── Panels: dark with glowing cyan border ── */
QFrame#panel {{
    background-color: {COLORS['bg_panel']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 2px;
    padding: 2px;
}}

QFrame#card {{
    background-color: {COLORS['bg_card']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    padding: 6px;
}}

/* ── Labels ── */
QLabel {{
    color: {COLORS['text_primary']};
    background: transparent;
    font-family: 'Courier New', 'Monaco', 'Menlo', 'Consolas', monospace;
}}

QLabel#title {{
    font-size: 18px;
    font-weight: bold;
    color: {COLORS['cyan']};
    letter-spacing: 6px;
    padding: 4px 8px;
    border-bottom: 1px solid {COLORS['cyan_dim']};
}}

QLabel#subtitle {{
    font-size: 12px;
    font-weight: bold;
    color: {COLORS['magenta']};
    letter-spacing: 3px;
    padding: 2px 4px;
    border-left: 3px solid {COLORS['magenta']};
    padding-left: 6px;
    background: {COLORS['bg_card']};
}}

QLabel#muted {{
    color: {COLORS['text_muted']};
    font-size: 10px;
}}

QLabel#value_positive {{
    color: {COLORS['green_bright']};
    font-weight: bold;
    font-size: 14px;
}}

QLabel#value_negative {{
    color: {COLORS['pink']};
    font-weight: bold;
    font-size: 14px;
}}

QLabel#signal_long {{
    color: {COLORS['green_bright']};
    font-size: 16px;
    font-weight: bold;
    padding: 6px 14px;
    border: 2px solid {COLORS['green_bright']};
    border-radius: 0px;
    background: {COLORS['green_dim']};
    letter-spacing: 4px;
}}

QLabel#signal_short {{
    color: {COLORS['pink']};
    font-size: 16px;
    font-weight: bold;
    padding: 6px 14px;
    border: 2px solid {COLORS['pink']};
    border-radius: 0px;
    background: {COLORS['pink_dim']};
    letter-spacing: 4px;
}}

/* ── Tables ── */
QTableWidget {{
    background-color: {COLORS['bg_panel']};
    gridline-color: {COLORS['grid']};
    border: 1px solid {COLORS['cyan_dim']};
    selection-background-color: #001a2a;
    selection-color: {COLORS['cyan']};
    font-size: 11px;
    font-family: 'Courier New', monospace;
}}

QTableWidget::item {{
    padding: 3px 6px;
    border-bottom: 1px solid {COLORS['grid']};
    color: {COLORS['text_primary']};
}}

QTableWidget::item:selected {{
    background-color: #001a2a;
    color: {COLORS['cyan']};
    border: 1px solid {COLORS['cyan_dim']};
}}

QHeaderView::section {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['magenta']};
    border: 1px solid {COLORS['cyan_dim']};
    border-bottom: 2px solid {COLORS['magenta_dim']};
    padding: 5px 6px;
    font-weight: bold;
    font-size: 10px;
    letter-spacing: 2px;
    font-family: 'Courier New', monospace;
}}

/* ── Buttons — neon outlined, Tron-style ── */
QPushButton {{
    background-color: transparent;
    color: {COLORS['cyan']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    padding: 6px 18px;
    font-weight: bold;
    font-size: 11px;
    letter-spacing: 2px;
    font-family: 'Courier New', monospace;
}}

QPushButton:hover {{
    background-color: {COLORS['cyan_glow']};
    border: 1px solid {COLORS['cyan']};
    color: {COLORS['cyan']};
}}

QPushButton:pressed {{
    background-color: {COLORS['cyan_dim']};
    color: {COLORS['bg_dark']};
    border: 1px solid {COLORS['cyan']};
}}

QPushButton#danger {{
    color: {COLORS['pink']};
    border-color: {COLORS['pink_dim']};
}}

QPushButton#danger:hover {{
    border-color: {COLORS['pink']};
    background-color: rgba(255, 0, 102, 0.15);
}}

/* ── Tab bar — neon underline style ── */
QTabWidget::pane {{
    border: 1px solid {COLORS['cyan_dim']};
    border-top: 2px solid {COLORS['cyan_dim']};
    background: {COLORS['bg_panel']};
    top: -1px;
}}

QTabBar {{
    background: {COLORS['bg_dark']};
}}

QTabBar::tab {{
    background: {COLORS['bg_dark']};
    color: {COLORS['text_muted']};
    border: 0px;
    border-right: 1px solid {COLORS['cyan_dim']};
    border-bottom: 3px solid transparent;
    padding: 8px 16px;
    margin-right: 0px;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 2px;
    font-family: 'Courier New', monospace;
    min-width: 80px;
}}

QTabBar::tab:selected {{
    background: {COLORS['bg_panel']};
    color: {COLORS['cyan']};
    border-bottom: 3px solid {COLORS['cyan']};
    border-right: 1px solid {COLORS['cyan_dim']};
}}

QTabBar::tab:hover:!selected {{
    background: {COLORS['bg_hover']};
    color: {COLORS['magenta']};
    border-bottom: 3px solid {COLORS['magenta_dim']};
}}

/* ── Scrollbars — thin neon lines ── */
QScrollBar:vertical {{
    background: {COLORS['bg_dark']};
    width: 6px;
    border: none;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {COLORS['cyan_dim']};
    border-radius: 0px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background: {COLORS['cyan']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background: {COLORS['bg_dark']};
    height: 6px;
    border: none;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {COLORS['cyan_dim']};
    border-radius: 0px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {COLORS['cyan']};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

/* ── Progress bars — neon fill ── */
QProgressBar {{
    background: {COLORS['bg_dark']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    text-align: center;
    color: {COLORS['cyan']};
    font-size: 10px;
    font-family: 'Courier New', monospace;
    height: 14px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {COLORS['cyan_dim']},
        stop:0.5 {COLORS['cyan']},
        stop:1 {COLORS['magenta']});
    border-radius: 0px;
}}

/* ── Splitters ── */
QSplitter::handle {{
    background: {COLORS['cyan_dim']};
}}

QSplitter::handle:horizontal {{
    width: 2px;
    background: {COLORS['cyan_dim']};
}}

QSplitter::handle:vertical {{
    height: 2px;
    background: {COLORS['cyan_dim']};
}}

QSplitter::handle:hover {{
    background: {COLORS['cyan']};
}}

/* ── Text / line edits ── */
QTextEdit, QPlainTextEdit {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    padding: 4px;
    font-size: 11px;
    font-family: 'Courier New', monospace;
    selection-background-color: {COLORS['cyan_dim']};
    selection-color: {COLORS['bg_dark']};
}}

QLineEdit {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    padding: 4px 8px;
    font-family: 'Courier New', monospace;
}}

QLineEdit:focus {{
    border: 1px solid {COLORS['cyan']};
    background: {COLORS['bg_hover']};
}}

/* ── Combo boxes ── */
QComboBox {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    padding: 4px 8px;
    font-family: 'Courier New', monospace;
}}

QComboBox::drop-down {{
    border: none;
    background: {COLORS['cyan_dim']};
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background: {COLORS['bg_card']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['cyan_dim']};
    selection-background-color: {COLORS['cyan_dim']};
    selection-color: {COLORS['cyan']};
}}

/* ── Tooltips ── */
QToolTip {{
    background: {COLORS['bg_card']};
    color: {COLORS['cyan']};
    border: 1px solid {COLORS['cyan']};
    padding: 4px 8px;
    font-size: 11px;
    font-family: 'Courier New', monospace;
}}

/* ── Status bar ── */
QStatusBar {{
    background: {COLORS['bg_dark']};
    color: {COLORS['cyan']};
    border-top: 1px solid {COLORS['cyan_dim']};
    font-size: 10px;
    font-family: 'Courier New', monospace;
    letter-spacing: 1px;
}}

QStatusBar::item {{
    border: none;
}}

/* ── Menu / context menus ── */
QMenu {{
    background: {COLORS['bg_card']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['cyan_dim']};
    font-family: 'Courier New', monospace;
}}

QMenu::item:selected {{
    background: {COLORS['cyan_dim']};
    color: {COLORS['cyan']};
}}

/* ── Checkboxes ── */
QCheckBox {{
    color: {COLORS['text_primary']};
    spacing: 6px;
    font-family: 'Courier New', monospace;
}}

QCheckBox::indicator {{
    width: 12px;
    height: 12px;
    border: 1px solid {COLORS['cyan_dim']};
    background: {COLORS['bg_dark']};
    border-radius: 0px;
}}

QCheckBox::indicator:checked {{
    background: {COLORS['cyan_dim']};
    border: 1px solid {COLORS['cyan']};
}}

/* ── Spin boxes ── */
QSpinBox, QDoubleSpinBox {{
    background: {COLORS['bg_input']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    padding: 3px;
    font-family: 'Courier New', monospace;
}}

/* ── Group boxes ── */
QGroupBox {{
    border: 1px solid {COLORS['cyan_dim']};
    border-radius: 0px;
    margin-top: 1ex;
    font-weight: bold;
    color: {COLORS['magenta']};
    font-size: 10px;
    letter-spacing: 2px;
    font-family: 'Courier New', monospace;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    color: {COLORS['magenta']};
    background: {COLORS['bg_dark']};
}}
"""


# CRT scanline overlay (applied as a semi-transparent pattern)
CRT_OVERLAY_CSS = f"""
QWidget#crt_overlay {{
    background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0, 0, 0, 0.12) 2px,
        rgba(0, 0, 0, 0.12) 4px
    );
}}
"""
