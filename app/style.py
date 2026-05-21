"""
Global QSS stylesheet for GURU Mobile Discovery.

Refined slate-blue palette with subtle depth, generous spacing, and a small
SVG icon helper used across MainWindow, MessageViews, and the dialogs.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QIcon


_assets_root: Optional[Path] = None


def init_assets_root(path: Path) -> None:
    """Called once from main(); enables icon() lookups from anywhere."""
    global _assets_root
    _assets_root = Path(path)


def _resolved_assets_root() -> Path:
    if _assets_root is not None:
        return _assets_root
    return Path(__file__).resolve().parent.parent / "assets"


def icon_path(name: str) -> Path:
    """Resolve an SVG icon path under assets/icons/ (no .svg suffix needed)."""
    base = _resolved_assets_root() / "icons"
    if name.endswith(".svg"):
        return base / name
    return base / f"{name}.svg"


def icon(name: str) -> QIcon:
    """Return a QIcon for an SVG in assets/icons/, or an empty QIcon if missing."""
    p = icon_path(name)
    return QIcon(str(p)) if p.is_file() else QIcon()


def logo_path(name: str) -> Path:
    """Resolve a PNG logo path under assets/logo/ (no .png suffix needed)."""
    base = _resolved_assets_root() / "logo"
    if name.endswith(".png"):
        return base / name
    return base / f"{name}.png"


STYLESHEET = """
/* =========================================================
   Base
   ========================================================= */
QWidget {
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #1f2937;
    background-color: #f6f8fb;
}

QMainWindow, QDialog {
    background-color: #f6f8fb;
}

QToolTip {
    background-color: #2c3441;
    color: #f5f7fa;
    border: 1px solid #2c3441;
    border-radius: 4px;
    padding: 4px 8px;
}

/* =========================================================
   Menu bar
   ========================================================= */
QMenuBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e1e6ed;
    padding: 2px 4px;
    color: #1f2937;
}

QMenuBar::item {
    background: transparent;
    padding: 6px 12px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #eaf0f8;
    color: #2c4a73;
}

QMenu {
    background-color: #ffffff;
    border: 1px solid #e1e6ed;
    border-radius: 6px;
    padding: 6px 4px;
}

QMenu::item {
    padding: 7px 22px 7px 16px;
    border-radius: 4px;
    margin: 1px 4px;
    color: #1f2937;
}

QMenu::item:selected {
    background-color: #eaf0f8;
    color: #2c4a73;
}

QMenu::separator {
    height: 1px;
    background-color: #e1e6ed;
    margin: 4px 8px;
}

/* =========================================================
   Buttons
   ========================================================= */
QPushButton {
    background-color: #4a73b8;
    color: white;
    border: 1px solid #3f64a3;
    border-radius: 5px;
    padding: 7px 16px;
    min-height: 20px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #5a85cc;
    border-color: #4a73b8;
}

QPushButton:pressed {
    background-color: #3a5e9e;
    border-color: #2f5189;
}

QPushButton:disabled {
    background-color: #c9d0db;
    color: #8b95a3;
    border-color: #c9d0db;
}

QPushButton:focus {
    outline: none;
}

/* Subtle / secondary text-button style: same shape, neutral surface */
QPushButton[class="secondary"] {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #d6dde6;
}

QPushButton[class="secondary"]:hover {
    background-color: #f3f6fa;
    border-color: #b9c4d2;
}

QPushButton[class="secondary"]:pressed {
    background-color: #e8eef5;
}

/* Compact icon-only button used in lists and toolbars */
QPushButton[class="icon-btn"] {
    background-color: transparent;
    color: #5a6575;
    border: 1px solid #d6dde6;
    border-radius: 5px;
    padding: 0;
}

QPushButton[class="icon-btn"]:hover {
    background-color: #eaf0f8;
    border-color: #4a73b8;
}

QPushButton[class="icon-btn"]:pressed {
    background-color: #d8e3f1;
}

/* =========================================================
   Inputs
   ========================================================= */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
    background-color: #ffffff;
    border: 1px solid #d6dde6;
    border-radius: 5px;
    padding: 7px 11px;
    min-height: 20px;
    selection-background-color: #4a73b8;
    selection-color: #ffffff;
    color: #1f2937;
}

QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
    border-color: #b9c4d2;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #4a73b8;
}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    background-color: #eef1f5;
    color: #8b95a3;
    border-color: #e1e6ed;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
    background-color: transparent;
}

QComboBox::down-arrow {
    width: 12px;
    height: 12px;
    /*COMBO_ARROW_IMAGE*/
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #e1e6ed;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: #eaf0f8;
    selection-color: #2c4a73;
    outline: none;
}

/* =========================================================
   Tree widgets
   ========================================================= */
/* Saved searches: scoped QSS so Qt still calls PE_IndicatorBranch (proxy paints chevrons).
   A global QTreeWidget stylesheet makes Qt paint branches via the style sheet engine and
   skips primitive painting — invisible disclosure icons. */
/* Whole-row selection needs square corners on ::item; Fusion + proxy on SavedSearchesTree. */
#SavedSearchesTree {
    background-color: #ffffff;
    border: 1px solid #e1e6ed;
    border-radius: 8px;
    padding: 0 6px 6px 6px;
    outline: none;
    show-decoration-selected: 1;
    selection-background-color: #4a73b8;
    selection-color: #ffffff;
}

#SavedSearchesTree::item {
    padding: 7px 10px;
    border-radius: 0;
    margin: 0;
    border: none;
    color: #1f2937;
}

#SavedSearchesTree::item:hover {
    background-color: #eaf0f8;
}

#SavedSearchesTree::item:selected:hover {
    background-color: #4a73b8;
    color: white;
}

#SavedSearchesTree::item:selected {
    background-color: #4a73b8;
    color: white;
}

#SavedSearchesTree::item:selected:!active {
    background-color: #4a73b8;
    color: white;
}

/* Do not style #SavedSearchesTree::branch — QSS on ::branch forces Qt's stylesheet branch path and PE_IndicatorBranch is never drawn (no proxy chevrons). */

/* Case / backup tree (no disclosure column — RootIsDecorated off, indentation 0) */
QTreeWidget#CaseImportTree {
    background-color: #ffffff;
    border: 1px solid #e1e6ed;
    border-radius: 8px;
    padding: 6px;
    outline: none;
    show-decoration-selected: 1;
    selection-background-color: #4a73b8;
    selection-color: #ffffff;
}

QTreeWidget#CaseImportTree::item {
    padding: 7px 10px;
    border-radius: 5px;
    margin: 1px 0;
    border: none;
    color: #1f2937;
}

QTreeWidget#CaseImportTree::item:hover {
    background-color: #eaf0f8;
}

QTreeWidget#CaseImportTree::item:selected:hover {
    background-color: #4a73b8;
    color: white;
}

QTreeWidget#CaseImportTree::item:selected {
    background-color: #4a73b8;
    color: white;
}

QTreeWidget#CaseImportTree::item:selected:!active {
    background-color: #4a73b8;
    color: white;
}

QHeaderView:horizontal::section {
    background-color: #f0f3f7;
    padding: 9px 11px;
    border: none;
    border-right: 1px solid #d8dfe8;
    border-bottom: 2px solid #c9d0db;
    font-weight: 600;
    color: #2c3441;
}

QHeaderView:horizontal::section:hover {
    background-color: #e6ecf3;
}

QHeaderView:horizontal::section:first {
    border-top-left-radius: 0;
}

QHeaderView:vertical::section {
    background-color: #f0f3f7;
    padding: 2px 6px;
    border: none;
    border-right: 1px solid #d8dfe8;
    border-bottom: 1px solid #eef1f5;
    font-weight: 600;
    color: #2c3441;
}

/* =========================================================
   Table widget
   ========================================================= */
QTableWidget {
    background-color: #ffffff;
    border: 1px solid #e1e6ed;
    border-radius: 8px;
    gridline-color: #eef1f5;
    selection-background-color: #4a73b8;
    selection-color: white;
}

QTableWidget::item {
    padding: 4px 9px;
    color: #1f2937;
}

QTableWidget::item:selected {
    background-color: #4a73b8;
    color: white;
}

QTableWidget::item:alternate {
    background-color: #fafbfd;
}

QTableWidget::item:alternate:selected {
    background-color: #4a73b8;
    color: white;
}

/* =========================================================
   Tab widget
   ========================================================= */
QTabWidget::pane {
    border: 1px solid #e1e6ed;
    border-radius: 8px;
    background-color: #ffffff;
    top: -1px;
}

QTabBar::tab {
    background-color: transparent;
    color: #6b7280;
    padding: 9px 20px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}

QTabBar::tab:selected {
    color: #4a73b8;
    border-bottom: 2px solid #4a73b8;
    font-weight: 600;
}

QTabBar::tab:hover:!selected {
    color: #2c4a73;
    background-color: #eef2f7;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

/* =========================================================
   List widget
   ========================================================= */
QListWidget {
    background-color: #ffffff;
    border: 1px solid #e1e6ed;
    border-radius: 8px;
    padding: 0 6px 6px 6px;
    outline: none;
}

QListWidget::item {
    padding: 8px 10px;
    border-radius: 5px;
    margin: 1px 0;
    color: #1f2937;
}

QListWidget::item:hover {
    background-color: #eaf0f8;
}

QListWidget::item:selected {
    background-color: #4a73b8;
    color: white;
}

QListWidget::item:selected:!active {
    background-color: #4a73b8;
    color: white;
}

/* =========================================================
   Scroll area & scroll bars
   ========================================================= */
QScrollArea {
    border: 1px solid #e1e6ed;
    border-radius: 8px;
    background-color: #ffffff;
}

QScrollBar:vertical {
    background-color: transparent;
    width: 12px;
    margin: 2px 2px 2px 0;
    border: none;
}

QScrollBar::handle:vertical {
    background-color: #c5cdd8;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background-color: #4a73b8;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 12px;
    margin: 0 2px 2px 2px;
    border: none;
}

QScrollBar::handle:horizontal {
    background-color: #c5cdd8;
    border-radius: 5px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #4a73b8;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: none;
}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* =========================================================
   Splitter
   ========================================================= */
QSplitter::handle {
    background-color: transparent;
    width: 4px;
    height: 4px;
}

QSplitter::handle:hover {
    background-color: #c9d4e2;
}

#MainCaseSplitter::handle {
    width: 0;
    height: 0;
    margin: 0;
    padding: 0;
    border: none;
}

/* =========================================================
   Progress bar
   ========================================================= */
QProgressBar {
    border: 1px solid #d6dde6;
    border-radius: 6px;
    text-align: center;
    background-color: #eef1f5;
    color: #2c3441;
    font-weight: 500;
    min-height: 18px;
}

QProgressBar::chunk {
    background-color: #4a73b8;
    border-radius: 5px;
}

/* =========================================================
   Labels
   ========================================================= */
QLabel {
    color: #1f2937;
    background: transparent;
}

QLabel[class="placeholder"] {
    color: #6b7280;
    font-size: 14px;
    padding: 8px;
}

QLabel[class="placeholder-title"] {
    color: #2c3441;
    font-size: 17px;
    font-weight: 600;
    padding: 4px;
}

QLabel[class="placeholder-subtitle"] {
    color: #6b7280;
    font-size: 13px;
    padding: 0 8px 4px 8px;
}

QLabel[class="section-heading"] {
    color: #2c3441;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 4px 2px;
}

/* =========================================================
   Message box
   ========================================================= */
QMessageBox {
    background-color: #ffffff;
}

QMessageBox QPushButton {
    min-width: 80px;
}

/* =========================================================
   Checkbox
   ========================================================= */
QCheckBox {
    spacing: 8px;
    color: #1f2937;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #c9d0db;
    border-radius: 4px;
    background-color: white;
}

QCheckBox::indicator:hover {
    border-color: #4a73b8;
}

QCheckBox::indicator:checked {
    background-color: #4a73b8;
    border: 2px solid #4a73b8;
    border-radius: 4px;
    /*CHECKBOX_IMAGE*/
    background-repeat: no-repeat;
    background-position: center;
}

QCheckBox::indicator:checked:hover {
    background-color: #3d63a0;
    border-color: #3d63a0;
}

QCheckBox::indicator:disabled {
    background-color: #eef1f5;
    border-color: #d6dde6;
}

QCheckBox::indicator:checked:disabled {
    background-color: #c9d0db;
    border-color: #c9d0db;
}

/* =========================================================
   SpinBox steppers
   ========================================================= */
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #f0f3f7;
    border: none;
    width: 20px;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #e1e6ed;
}

QSpinBox::up-arrow, QSpinBox::down-arrow {
    width: 8px;
    height: 8px;
}
"""


def _svg_to_png_data_uri(
    svg_text: str,
    render_size: int,
    output_size: Optional[int] = None,
) -> Optional[str]:
    """Rasterise an SVG (provided as a string) to a PNG and return a base64 data URI.

    render_size: the QImage size (px) to render the SVG into. Use 2x the
    intended on-screen size for crisper edges on HiDPI displays.
    output_size: optional final size (px) to scale the rendered image to;
    defaults to render_size if not provided.

    Returns None if QtSvg is unavailable or rendering fails.
    """
    try:
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtSvg import QSvgRenderer
    except ImportError:
        return None

    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        return None

    img = QImage(render_size, render_size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
    finally:
        painter.end()

    if output_size and output_size != render_size:
        img = img.scaled(
            output_size,
            output_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        return None
    b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
    return f"data:image/png;base64,{b64}"


# White checkmark, drawn larger so it stays readable inside the 14x14 inner
# content area of the QCheckBox indicator. Rasterised at 2x for HiDPI.
_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14">'
    '<path fill="none" stroke="#ffffff" stroke-width="2.6" '
    'stroke-linecap="round" stroke-linejoin="round" '
    'd="M2.5 7.4 L5.6 10.2 L11.4 3.6"/>'
    "</svg>"
)


def get_stylesheet(assets_root: Optional[Path] = None) -> str:
    """
    Return the global stylesheet, with checkbox checkmark and combo dropdown
    arrow images rasterised in-memory and inlined as base64 PNG data URIs.

    PNG data URIs work reliably across all paths (including those with '&')
    and Qt platform styles. The check image is white-on-blue to be visible
    against the filled :checked indicator background.

    assets_root: project root's assets folder (or PyInstaller _MEIPASS/assets).
    """
    qss = STYLESHEET

    check_uri = _svg_to_png_data_uri(_CHECK_SVG, render_size=28, output_size=14)
    if check_uri:
        qss = qss.replace(
            "    /*CHECKBOX_IMAGE*/",
            f'    background-image: url({check_uri});',
        )
    else:
        qss = qss.replace("    /*CHECKBOX_IMAGE*/", "")

    root = Path(assets_root) if assets_root is not None else _resolved_assets_root()
    arrow_path = root / "icons" / "chevron-down.svg"
    arrow_uri: Optional[str] = None
    if arrow_path.is_file():
        try:
            arrow_text = arrow_path.read_text(encoding="utf-8")
            arrow_uri = _svg_to_png_data_uri(arrow_text, render_size=24, output_size=12)
        except OSError:
            arrow_uri = None
    if arrow_uri:
        qss = qss.replace(
            "    /*COMBO_ARROW_IMAGE*/",
            f'    image: url({arrow_uri});',
        )
    else:
        qss = qss.replace("    /*COMBO_ARROW_IMAGE*/", "")

    return qss


# Backwards-compat shim — kept in case other modules imported it.
def _svg_to_png_uri(svg_path: Path, size: int) -> Optional[str]:
    """Deprecated: rasterise an SVG file and write a temp PNG. Prefer the
    in-memory data-URI helper. Returns a file:// URI."""
    if not svg_path.is_file():
        return None
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtSvg import QSvgRenderer
    except ImportError:
        return None
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        return None
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    tmp_dir = Path(tempfile.gettempdir()) / "guru_qss_assets"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    out_path = tmp_dir / f"{svg_path.stem}_{size}.png"
    if not img.save(str(out_path), "PNG"):
        return None
    return out_path.resolve().as_uri()
