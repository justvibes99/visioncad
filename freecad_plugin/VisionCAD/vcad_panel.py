"""Vision CAD chat panel — Qt dock widget with Claude integration."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid

import FreeCAD
import FreeCADGui as Gui

from PySide2 import QtCore, QtGui, QtWidgets

CONFIG_PATH = os.path.expanduser("~/.visioncad/config.json")

def _find_project_root():
    """Find the VisionCAD repo root. Checks:
    1. VISIONCAD_ROOT environment variable
    2. config.json repo_root field
    3. If this plugin is a symlink, resolve to the repo
    """
    env = os.environ.get("VISIONCAD_ROOT")
    if env and os.path.isdir(env):
        return env

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            root = json.load(f).get("repo_root")
            if root and os.path.isdir(root):
                return root

    # If the plugin dir is a symlink, follow it back to the repo
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    real_dir = os.path.realpath(plugin_dir)
    if real_dir != plugin_dir:
        # Symlink — the repo root is the parent of the plugin dir
        # (plugin is at repo/freecad_plugin/VisionCAD/)
        candidate = os.path.dirname(os.path.dirname(real_dir))
        if os.path.exists(os.path.join(candidate, "config.py")):
            return candidate

    return None

def _find_claude():
    env = os.environ.get("VISIONCAD_CLAUDE")
    if env and os.path.isfile(env):
        return env
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            p = json.load(f).get("claude_path")
            if p and os.path.isfile(p):
                return p
    found = shutil.which("claude")
    if found:
        return found
    for c in [os.path.expanduser("~/.local/bin/claude"), "/usr/local/bin/claude"]:
        if os.path.isfile(c):
            return c
    return None

FREECAD_AI_DIR = _find_project_root() or os.path.expanduser("~/code/freecad-ai")
SYSTEM_PROMPT_FILE = os.path.join(FREECAD_AI_DIR, "system_prompt.txt")
CLAUDE_PATH = _find_claude() or "claude"

_panel_instance = None


def _load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _get_projects_dir():
    """Return the projects directory, prompting on first use."""
    config = _load_config()
    if "projects_dir" in config and os.path.isdir(config["projects_dir"]):
        return config["projects_dir"]

    # First-run: ask user where to save projects
    base = QtWidgets.QFileDialog.getExistingDirectory(
        None, "Choose where to save VisionCAD projects",
        os.path.expanduser("~"))
    if not base:
        # Fall back to default
        base = os.path.expanduser("~/Documents")

    projects_dir = os.path.join(base, "VisionCAD")
    os.makedirs(projects_dir, exist_ok=True)
    config["projects_dir"] = projects_dir
    _save_config(config)
    return projects_dir


def _md_to_html(text):
    """Convert basic markdown to HTML for QLabel."""
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', t)
    t = re.sub(r'`(.+?)`', r'<code style="background:#e7e5e4;padding:1px 4px;border-radius:3px">\1</code>', t)
    t = re.sub(r'^- ', '&nbsp;&nbsp;• ', t, flags=re.MULTILINE)
    t = re.sub(r'^(\d+)\. ', r'&nbsp;&nbsp;\1. ', t, flags=re.MULTILINE)
    t = t.replace('\n', '<br>')
    return t


class ChatMessage(QtWidgets.QFrame):
    def __init__(self, text, is_user=False, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._raw_text = text
        self._is_user = is_user
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)

        self.label = QtWidgets.QLabel(self._render())
        self.label.setTextFormat(QtCore.Qt.RichText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.label.setMaximumWidth(500)

        if is_user:
            self.label.setStyleSheet("""
                QLabel { background: #b45309; color: white; padding: 8px 12px;
                    border-radius: 10px; border-bottom-right-radius: 3px; font-size: 13px; }
            """)
            layout.addStretch()
            layout.addWidget(self.label)
        else:
            self.label.setStyleSheet("""
                QLabel { background: #f5f5f4; color: #1c1917; padding: 8px 12px;
                    border-radius: 10px; border-bottom-left-radius: 3px; font-size: 13px;
                    border: 1px solid #e7e5e4; }
            """)
            layout.addWidget(self.label)
            layout.addStretch()

    def _render(self):
        if self._is_user:
            escaped = self._raw_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f'<span style="color:white">{escaped}</span>'
        return f'<span style="color:#1c1917">{_md_to_html(self._raw_text)}</span>'

    def append_text(self, text):
        self._raw_text += text
        self.label.setText(self._render())
        self.label.adjustSize()
        self.adjustSize()


class VisionCADPanel(QtWidgets.QDockWidget):
    new_message_signal = QtCore.Signal(str, bool)
    append_message_signal = QtCore.Signal(str)
    set_typing_signal = QtCore.Signal(bool)
    reload_doc_signal = QtCore.Signal()
    reset_assistant_signal = QtCore.Signal()
    status_signal = QtCore.Signal(str)
    clear_status_signal = QtCore.Signal()

    def __init__(self):
        super().__init__("Vision CAD", Gui.getMainWindow())
        self.setObjectName("VisionCADPanel")
        self.setMinimumWidth(380)

        self.claude_proc = None
        self.current_assistant_msg = None
        self.reference_image = None
        self.project_id = None
        self.project_dir = None
        self._responding = False

        self.new_message_signal.connect(self._add_message)
        self.append_message_signal.connect(self._append_to_assistant)
        self.set_typing_signal.connect(self._set_typing)
        self.reload_doc_signal.connect(self._reload_document)
        self.reset_assistant_signal.connect(self._reset_assistant_msg)
        self.status_signal.connect(self._add_status)
        self.clear_status_signal.connect(self._clear_status)

        # Build UI
        container = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header with integrated actions
        header = QtWidgets.QFrame()
        header.setStyleSheet("QFrame { background: #fafaf9; border-bottom: 1px solid #e7e5e4; }")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(12, 6, 8, 6)
        header_layout.setSpacing(6)
        title = QtWidgets.QLabel("Vision CAD")
        title.setStyleSheet("font-weight: 700; font-size: 14px; color: #1c1917;")
        header_layout.addWidget(title)
        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setStyleSheet("color: #a8a29e; font-size: 10px;")
        header_layout.addWidget(self.status_dot)
        header_layout.addStretch()
        for label, cmd in [
            ("New Project", "_new_project"),
            ("Create Docs", "_generate_build"),
        ]:
            btn = QtWidgets.QPushButton(label)
            btn.setStyleSheet("""
                QPushButton { background: transparent; border: 1px solid #d6d3d1; border-radius: 6px;
                    padding: 5px 14px; font-size: 12px; font-weight: 600; color: #44403c; }
                QPushButton:hover { background: #e7e5e4; border-color: #a8a29e; color: #1c1917; }
            """)
            btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            btn.clicked.connect(getattr(self, cmd))
            header_layout.addWidget(btn)
        main_layout.addWidget(header)

        # Reference image thumbnail
        self.image_label = QtWidgets.QLabel()
        self.image_label.setMaximumHeight(120)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet("background: #f5f5f4; border-bottom: 1px solid #e7e5e4;")
        self.image_label.hide()
        main_layout.addWidget(self.image_label)

        # Chat messages
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: white; }")
        self.messages_widget = QtWidgets.QWidget()
        self.messages_layout = QtWidgets.QVBoxLayout(self.messages_widget)
        self.messages_layout.setAlignment(QtCore.Qt.AlignTop)
        self.messages_layout.setSpacing(4)
        self.messages_layout.setContentsMargins(4, 8, 4, 8)

        self.typing_label = QtWidgets.QLabel("  ...")
        self.typing_label.setStyleSheet("""
            QLabel { background: #f5f5f4; color: #a8a29e; padding: 6px 12px;
                border-radius: 10px; border-bottom-left-radius: 3px;
                font-size: 16px; font-weight: bold; border: 1px solid #e7e5e4; }
        """)
        self.typing_label.setFixedWidth(60)
        self.typing_label.hide()
        self.messages_layout.addWidget(self.typing_label)

        scroll.setWidget(self.messages_widget)
        self.scroll_area = scroll
        main_layout.addWidget(scroll, 1)

        # Input
        input_frame = QtWidgets.QFrame()
        input_frame.setStyleSheet("QFrame { background: #fafaf9; border-top: 1px solid #e7e5e4; }")
        input_layout = QtWidgets.QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(6)

        self._pending_images = []

        attach_btn = QtWidgets.QPushButton("◩")
        attach_btn.setToolTip("Attach image")
        attach_btn.setStyleSheet("""
            QPushButton { background: transparent; border: 1px solid #d6d3d1; border-radius: 8px;
                padding: 6px 10px; font-size: 14px; color: #78716c; }
            QPushButton:hover { background: #e7e5e4; color: #1c1917; }
        """)
        attach_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        attach_btn.clicked.connect(self._attach_image)
        input_layout.addWidget(attach_btn)

        self.input_field = QtWidgets.QLineEdit()
        self.input_field.setPlaceholderText("Describe changes to the design...")
        self.input_field.setStyleSheet("""
            QLineEdit { border: 1px solid #d6d3d1; border-radius: 8px; padding: 8px 12px;
                font-size: 13px; background: white; color: #1c1917; }
            QLineEdit:focus { border-color: #b45309; }
        """)
        self.input_field.returnPressed.connect(self._send_from_input)
        input_layout.addWidget(self.input_field)

        send_btn = QtWidgets.QPushButton("Send")
        send_btn.setStyleSheet("""
            QPushButton { background: #b45309; color: white; border: none; border-radius: 8px;
                padding: 8px 16px; font-weight: 600; font-size: 13px; }
            QPushButton:hover { background: #92400e; }
        """)
        send_btn.clicked.connect(self._send_from_input)
        input_layout.addWidget(send_btn)

        main_layout.addWidget(input_frame)
        self.setWidget(container)

    # ---- Message display ----

    def _add_message(self, text, is_user):
        msg = ChatMessage(text, is_user)
        idx = self.messages_layout.indexOf(self.typing_label)
        self.messages_layout.insertWidget(idx, msg)
        if not is_user:
            self.current_assistant_msg = msg
        self._scroll_to_bottom()

    def _append_to_assistant(self, text):
        if self.current_assistant_msg:
            self.current_assistant_msg.append_text(text)
        else:
            self._add_message(text, False)
        self._scroll_to_bottom()

    def _reset_assistant_msg(self):
        self.current_assistant_msg = None

    def _add_status(self, text):
        """Add a small muted status line (tool progress). Replaces the previous one."""
        self._clear_status()
        label = QtWidgets.QLabel(text)
        label.setObjectName("_vcad_status")
        label.setStyleSheet(
            "QLabel { color: #a8a29e; font-size: 11px; font-style: italic; padding: 2px 12px; }")
        idx = self.messages_layout.indexOf(self.typing_label)
        self.messages_layout.insertWidget(idx, label)
        self._scroll_to_bottom()

    def _clear_status(self):
        """Remove all status labels."""
        for i in reversed(range(self.messages_layout.count())):
            widget = self.messages_layout.itemAt(i).widget()
            if widget and widget.objectName() == "_vcad_status":
                self.messages_layout.removeWidget(widget)
                widget.deleteLater()

    def _emit_status(self, text):
        self.status_signal.emit(text)

    @staticmethod
    def _tool_label(tool_name):
        labels = {
            "Read": "Reading file...",
            "Edit": "Editing file...",
            "Write": "Writing file...",
            "Bash": "Running command...",
            "Glob": "Searching files...",
            "Grep": "Searching code...",
            "ToolSearch": None,
        }
        return labels.get(tool_name, f"Using {tool_name}...")

    def _set_typing(self, visible):
        self.typing_label.setVisible(visible)
        self.input_field.setEnabled(not visible)
        self.status_dot.setStyleSheet(
            "color: #4ade80; font-size: 10px;" if visible else "color: #a8a29e; font-size: 10px;"
        )
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        self.messages_widget.adjustSize()
        QtCore.QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()))

    # ---- User actions ----

    def _send_message(self, text):
        if not text.strip() or self._responding:
            return
        self._detect_project()
        self.new_message_signal.emit(text, True)
        self.current_assistant_msg = None
        self._responding = True
        self.set_typing_signal.emit(True)

        if self.claude_proc is None or self.claude_proc.poll() is not None:
            self._start_claude()

        # Prepend live document context so Claude always knows current state
        context = self._get_document_context()
        full_text = f"{context}\n\n{text}" if context else text

        msg = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": full_text}],
            },
        })
        try:
            self.claude_proc.stdin.write((msg + "\n").encode())
            self.claude_proc.stdin.flush()
        except OSError as e:
            self.new_message_signal.emit(f"Error sending: {e}", False)
            self.set_typing_signal.emit(False)
            self._responding = False

    def _attach_image(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Attach Images", "",
            "Images (*.png *.jpg *.jpeg *.webp);;All Files (*)")
        if paths:
            self._pending_images.extend(paths)
            names = [os.path.basename(p) for p in paths]
            self.input_field.setPlaceholderText(f"📎 {', '.join(names)} — add message and send")

    def _send_from_input(self):
        text = self.input_field.text().strip()
        images = self._pending_images
        if text or images:
            self.input_field.clear()
            self.input_field.setPlaceholderText("Describe changes to the design...")
            self._pending_images = []
            # Prepend image paths to the message for Claude
            if images:
                img_prefix = " ".join(os.path.abspath(p) for p in images)
                text = f"{img_prefix} {text}" if text else f"{img_prefix} Describe this image."
            self._send_message(text)

    def _open_project_folder(self):
        self._detect_project()
        path = self.project_dir or _get_projects_dir()
        subprocess.Popen(["open", path])

    def _new_project(self):
        """Start a new project: ask for name, optionally load image, then generate."""
        # Ask for project name first
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Project", "Project name:")
        if not ok or not name.strip():
            return
        name = name.strip()

        # Ask if they have reference images
        reply = QtWidgets.QMessageBox.question(
            self, "Reference Images",
            "Do you have reference images?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)

        image_paths = []
        if reply == QtWidgets.QMessageBox.Yes:
            paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Load Reference Images (select one or more)", "",
                "Images (*.png *.jpg *.jpeg *.webp);;All Files (*)")
            if not paths:
                return
            image_paths = paths

        # Ask for description/notes — required for text-only, optional with images
        description = ""
        if image_paths:
            description, ok = QtWidgets.QInputDialog.getMultiLineText(
                self, "Notes (optional)",
                "Overall dimensions, wood species, edge profiles,\njoinery details, or other build notes:")
            if ok:
                description = description.strip()
        else:
            description, ok = QtWidgets.QInputDialog.getMultiLineText(
                self, "Describe the piece",
                "Describe the piece to build — include overall dimensions,\nstyle, edge profiles, joinery, and construction details:")
            if not ok or not description.strip():
                return
            description = description.strip()

        # Reset session — abort if user cancels save dialog
        prev_doc = FreeCAD.ActiveDocument
        self._new_chat()
        if FreeCAD.ActiveDocument is prev_doc and prev_doc is not None:
            return  # user cancelled

        # Create project directory
        dir_name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '-').lower()
        if not dir_name:
            dir_name = uuid.uuid4().hex[:8]
        projects_dir = _get_projects_dir()
        self.project_dir = os.path.join(projects_dir, dir_name)
        if os.path.exists(self.project_dir):
            dir_name = f"{dir_name}-{uuid.uuid4().hex[:4]}"
            self.project_dir = os.path.join(projects_dir, dir_name)
        self.project_id = dir_name
        os.makedirs(self.project_dir, exist_ok=True)

        # Set up project meta
        meta = {
            "id": self.project_id,
            "name": name,
            "status": "new",
            "created": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        if description:
            meta["description"] = description

        if image_paths:
            image_files = []
            for idx, img_path in enumerate(image_paths):
                ext = os.path.splitext(img_path)[1]
                fname = f"source{ext}" if idx == 0 else f"source_{idx+1}{ext}"
                dest = os.path.join(self.project_dir, fname)
                shutil.copy2(img_path, dest)
                image_files.append(fname)
            self.reference_image = os.path.join(self.project_dir, image_files[0])
            meta["image"] = image_files[0]
            if len(image_files) > 1:
                meta["images"] = image_files
            pixmap = QtGui.QPixmap(image_paths[0]).scaledToHeight(110, QtCore.Qt.SmoothTransformation)
            self.image_label.setPixmap(pixmap)
            self.image_label.show()

        with open(os.path.join(self.project_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

        self.new_message_signal.emit(f"Project created: {name}", False)

        # Auto-start model generation
        self._generate_model()

    def _new_chat(self):
        """Kill the Claude process, close current document, and clear chat."""
        # Prompt to save and close the active FreeCAD document
        doc = FreeCAD.ActiveDocument
        if doc:
            if doc.Modified:
                reply = QtWidgets.QMessageBox.question(
                    self, "Unsaved Changes",
                    f"Save changes to {doc.Label} before closing?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel)
                if reply == QtWidgets.QMessageBox.Cancel:
                    return
                if reply == QtWidgets.QMessageBox.Yes:
                    doc.save()
            FreeCAD.closeDocument(doc.Name)

        if self.claude_proc and self.claude_proc.poll() is None:
            self.claude_proc.stdin.close()
            self.claude_proc.terminate()
            self.claude_proc = None
        if hasattr(self, '_master_fd'):
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        self._responding = False
        self.current_assistant_msg = None
        self.reference_image = None
        self.project_id = None
        self.project_dir = None
        self.image_label.hide()
        # Clear all chat messages (keep typing_label)
        for i in reversed(range(self.messages_layout.count())):
            widget = self.messages_layout.itemAt(i).widget()
            if widget and widget is not self.typing_label:
                self.messages_layout.removeWidget(widget)
                widget.deleteLater()
        self.set_typing_signal.emit(False)
        self.new_message_signal.emit("New session started.", False)

    def _get_document_context(self):
        """Return a snapshot of the current FreeCAD document state."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            return "[No document open]"

        lines = [f"[Document: {doc.FileName or doc.Name}]"]

        objects = doc.Objects
        if objects:
            obj_descs = []
            for obj in objects:
                desc = f"{obj.Label} ({obj.TypeId})"
                if hasattr(obj, "Shape") and hasattr(obj.Shape, "BoundBox"):
                    bb = obj.Shape.BoundBox
                    if bb.isValid():
                        desc += f" [{bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f} mm]"
                obj_descs.append(desc)
            lines.append(f"[Objects: {', '.join(obj_descs)}]")
        else:
            lines.append("[Objects: none]")

        if self.project_id:
            lines.append(f"[Project: {self.project_id} → {self.project_dir}]")
            # List project files
            if self.project_dir and os.path.isdir(self.project_dir):
                files = [f for f in os.listdir(self.project_dir) if not f.startswith(("__", "."))]
                if files:
                    lines.append(f"[Project files: {', '.join(sorted(files))}]")

        if self.reference_image:
            lines.append(f"[Reference image: {self.reference_image}]")

        return "\n".join(lines)

    def _load_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Reference Image", "",
            "Images (*.png *.jpg *.jpeg *.webp);;All Files (*)")
        if not path:
            return

        # Ask for project name
        default_name = os.path.splitext(os.path.basename(path))[0].replace("_", " ").title()
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Project", "Project name:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()

        # Sanitize for directory name
        dir_name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '-').lower()
        if not dir_name:
            dir_name = uuid.uuid4().hex[:8]

        # Create project directory
        projects_dir = _get_projects_dir()
        self.project_dir = os.path.join(projects_dir, dir_name)
        if os.path.exists(self.project_dir):
            dir_name = f"{dir_name}-{uuid.uuid4().hex[:4]}"
            self.project_dir = os.path.join(projects_dir, dir_name)
        self.project_id = dir_name
        os.makedirs(self.project_dir, exist_ok=True)

        # Copy image into project
        ext = os.path.splitext(path)[1]
        dest = os.path.join(self.project_dir, f"source{ext}")
        shutil.copy2(path, dest)
        self.reference_image = dest

        # Create meta.json
        meta = {
            "id": self.project_id,
            "name": name,
            "image": f"source{ext}",
            "status": "new",
            "created": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        with open(os.path.join(self.project_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

        pixmap = QtGui.QPixmap(path).scaledToHeight(110, QtCore.Qt.SmoothTransformation)
        self.image_label.setPixmap(pixmap)
        self.image_label.show()
        self.new_message_signal.emit(f"Project created: {name}", False)

    def _generate_model(self):
        if not self.project_dir:
            self.new_message_signal.emit("No project — use New Project first.", False)
            return
        if self._responding:
            return
        self._responding = True
        self.set_typing_signal.emit(True)
        self.new_message_signal.emit("Generating model...", False)
        # Collect all images from the project directory
        image_paths = []
        if self.project_dir:
            meta_path = os.path.join(self.project_dir, "meta.json")
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
                if "images" in meta:
                    image_paths = [os.path.join(self.project_dir, f) for f in meta["images"]]
                elif "image" in meta:
                    image_paths = [os.path.join(self.project_dir, meta["image"])]
        threading.Thread(
            target=self._run_generate, args=(image_paths, self.project_dir),
            daemon=True).start()

    def _run_generate(self, image_paths, project_dir):
        """Run generate.py model in a subprocess (handles its own Claude call)."""
        env = os.environ.copy()
        env["HOME"] = os.path.expanduser("~")
        env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE"):
            env.pop(key, None)

        venv_python = os.path.join(FREECAD_AI_DIR, ".venv", "bin", "python")
        cmd = [venv_python, os.path.join(FREECAD_AI_DIR, "generate.py"), "model"]
        if image_paths:
            cmd.extend(image_paths)
        # Always include description if available
        meta_path = os.path.join(project_dir, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            desc = meta.get("description", "")
            if desc:
                cmd.extend(["--description", desc])
            elif not image_paths:
                cmd.extend(["--description", meta.get("name", "")])
        cmd.extend(["--output-dir", project_dir])
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=FREECAD_AI_DIR,
            )
            for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self.status_signal.emit(text)
            proc.wait()

            if proc.returncode == 0:
                # Find the generated FCStd and open it
                for f in os.listdir(project_dir):
                    if f.endswith(".FCStd"):
                        fcstd = os.path.join(project_dir, f)
                        self.new_message_signal.emit(f"Model generated: {f}", False)
                        self.reload_doc_signal.emit()
                        break
                else:
                    self.new_message_signal.emit("Model script ran but no .FCStd found.", False)
            else:
                self.new_message_signal.emit("Generation failed — check FreeCAD console.", False)
        except Exception as e:
            self.new_message_signal.emit(f"Error: {e}", False)
        finally:
            self.set_typing_signal.emit(False)
            self._responding = False

    def _detect_project(self):
        """Try to detect project from active document path or current state."""
        if self.project_id:
            return True
        doc = FreeCAD.ActiveDocument
        if doc and doc.FileName:
            # Check if the file is inside any projects directory
            path = os.path.normpath(doc.FileName)
            # Check configured projects dir
            projects_dir = os.path.normpath(_get_projects_dir())
            if path.startswith(projects_dir + os.sep):
                rel = path[len(projects_dir) + 1:]
                self.project_id = rel.split(os.sep)[0]
                self.project_dir = os.path.join(projects_dir, self.project_id)
                return True
            # Also check legacy location
            legacy = os.path.normpath(os.path.join(FREECAD_AI_DIR, "projects"))
            if path.startswith(legacy + os.sep):
                rel = path[len(legacy) + 1:]
                self.project_id = rel.split(os.sep)[0]
                self.project_dir = os.path.join(legacy, self.project_id)
                return True
        return False

    def _generate_build(self):
        if not self._detect_project():
            self.new_message_signal.emit("No project found — load an image first.", False)
            return
        if self._responding:
            return
        self._responding = True
        self.set_typing_signal.emit(True)
        self.new_message_signal.emit("Generating build docs...", False)
        threading.Thread(
            target=self._run_build, args=(self.project_id,), daemon=True).start()

    def _run_build(self, project_id):
        """Run generate.py build to create cut sheet + build instructions + PDF."""
        env = os.environ.copy()
        env["HOME"] = os.path.expanduser("~")
        env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE"):
            env.pop(key, None)

        proj_dir = self.project_dir
        venv_python = os.path.join(FREECAD_AI_DIR, ".venv", "bin", "python")
        # Import here to avoid importing config.py at FreeCAD module load time
        import sys
        if FREECAD_AI_DIR not in sys.path:
            sys.path.insert(0, FREECAD_AI_DIR)
        from config import FREECAD_CMD
        freecadcmd = FREECAD_CMD

        # Step 1: Re-run model script to refresh FCStd and STEP
        script_path = os.path.join(proj_dir, "_generated_model.py")
        if not os.path.exists(script_path):
            self.new_message_signal.emit("Error: No _generated_model.py found. Generate a model first.", False)
            self.set_typing_signal.emit(False)
            self._responding = False
            return

        self.status_signal.emit("Building model...")
        subprocess.run(
            [freecadcmd, script_path],
            capture_output=True, text=True, env=env, cwd=FREECAD_AI_DIR, timeout=120)

        fcstd = os.path.join(proj_dir, "source.FCStd")
        if not os.path.exists(fcstd):
            self.new_message_signal.emit("Error: Model script did not produce source.FCStd.", False)
            self.set_typing_signal.emit(False)
            self._responding = False
            return

        # Step 2: Generate cut list from the FCStd geometry (not the script's
        # hardcoded cut list, which can drift from the actual geometry)
        self.status_signal.emit("Generating cut list from model...")
        wrapper = os.path.join(proj_dir, "_run_cut_list.py")
        with open(wrapper, "w") as wf:
            wf.write(f"import sys; sys.path.insert(0, {FREECAD_AI_DIR!r})\n")
            wf.write(f"from auto_cut_list import generate_cut_list\n")
            wf.write(f"import json\n")
            wf.write(f"cuts = generate_cut_list({fcstd!r})\n")
            wf.write(f"print('=== CUT_JSON ===')\n")
            wf.write(f"print(json.dumps(cuts))\n")

        got_cut_list = False
        MAX_ATTEMPTS = 2
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                self.status_signal.emit(f"Retrying cut list (attempt {attempt})...")
            result = subprocess.run(
                [freecadcmd, wrapper],
                capture_output=True, text=True, env=env, cwd=FREECAD_AI_DIR, timeout=60)
            for i, line in enumerate(result.stdout.split("\n")):
                if "CUT_JSON" in line:
                    next_line = result.stdout.split("\n")[i + 1].strip()
                    if next_line:
                        with open(os.path.join(proj_dir, "cut_list.json"), "w") as f:
                            f.write(next_line)
                        got_cut_list = True
                    break
            if got_cut_list:
                break

        try:
            os.remove(wrapper)
        except OSError:
            pass

        if not got_cut_list:
            self.new_message_signal.emit(
                "Error: Could not generate cut list from source.FCStd. "
                "Check that the model has valid geometry.", False)
            self.set_typing_signal.emit(False)
            self._responding = False
            return

        # Step 2: Export projection from the FCStd
        self.status_signal.emit("Exporting projection...")
        proj_svg = os.path.join(proj_dir, "source_projection.svg")
        with open(os.path.join(tempfile.gettempdir(), "freecad_export_config.txt"), "w") as f:
            f.write(f"{fcstd}\n{proj_svg}\n")
        subprocess.run(
            [freecadcmd, os.path.join(FREECAD_AI_DIR, "export_projection.py")],
            capture_output=True, env=env, cwd=FREECAD_AI_DIR, timeout=60)

        # Step 3: Generate build docs (cut sheet + Claude build instructions + PDF)
        self.status_signal.emit("Generating build docs...")
        cmd = [venv_python, os.path.join(FREECAD_AI_DIR, "generate.py"), "build", proj_dir]

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=FREECAD_AI_DIR,
            )
            for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self.status_signal.emit(text)
            proc.wait()

            if proc.returncode == 0:
                for f in os.listdir(proj_dir):
                    if f.endswith(".pdf"):
                        self.new_message_signal.emit(f"Build file ready: {f}", False)
                        subprocess.Popen(["open", os.path.join(proj_dir, f)])
                        break
                else:
                    self.new_message_signal.emit("Build docs generated (no PDF — check for SVGs).", False)
            else:
                self.new_message_signal.emit("Build failed — check FreeCAD console.", False)
        except subprocess.TimeoutExpired:
            self.new_message_signal.emit("Build timed out.", False)
        except Exception as e:
            self.new_message_signal.emit(f"Error: {e}", False)
        finally:
            self.set_typing_signal.emit(False)
            self._responding = False

    # ---- Claude process ----

    def _build_context_prompt(self):
        return (
            "\n\n--- FREECAD CONTEXT ---\n"
            "You are running as Vision CAD inside FreeCAD.\n"
            f"Code directory: {FREECAD_AI_DIR}\n"
            f"Projects directory: {_get_projects_dir()}\n\n"
            "PROJECT STRUCTURE:\n"
            "Each project lives in projects/<id>/ and contains:\n"
            "  meta.json              — project metadata (name, status, files)\n"
            "  source.png/jpg         — reference image\n"
            "  _generated_model.py    — FreeCAD script (write/update this)\n"
            "  <name>.FCStd / .step   — model outputs\n"
            "  cut_list.json          — cut list (generated by the script)\n"
            "  <name>_cutsheet.svg    — cut sheet\n"
            "  <name>.pdf             — combined build document\n\n"
            "WORKFLOW:\n"
            "- All generated files go in the project directory (shown in [Project: ...] context)\n"
            "- To create a new model: Write _generated_model.py in the project dir, run with freecadcmd\n"
            "- To modify an existing model: use the Edit tool to make targeted changes to\n"
            "  _generated_model.py — NEVER rewrite the entire file. Find the specific section\n"
            "  that needs changing and edit just that part. Then re-run with freecadcmd.\n"
            f"- To generate build docs: python {FREECAD_AI_DIR}/process_task.py <project_id> build\n"
            "- Update meta.json status after completing steps (generating → done)\n\n"
            "CRITICAL: When modifying models, use Edit (not Write) on _generated_model.py.\n"
            "The script is large. Rewriting it entirely is slow and error-prone.\n"
            "Read it, find the relevant section, and Edit just that section.\n\n"
            "Each user message starts with [Document: ...], [Objects: ...], and [Project: ...]\n"
            "showing the live FreeCAD and project state. Use this to know what exists and what changed.\n"
            "Keep chat replies short and clear — just what changed and what to do next.\n"
        )

    def _start_claude(self):
        import pty as pty_mod

        env = os.environ.copy()
        env["HOME"] = os.path.expanduser("~")
        env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")

        cmd = [
            CLAUDE_PATH, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--system-prompt-file", SYSTEM_PROMPT_FILE,
            "--append-system-prompt", self._build_context_prompt(),
        ]

        # Use a PTY for stdout to prevent Node.js pipe buffering.
        # stream-json still gives us clean JSON lines — no terminal UI.
        master_fd, slave_fd = pty_mod.openpty()

        try:
            self.claude_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=FREECAD_AI_DIR,
            )
            os.close(slave_fd)
        except OSError as e:
            os.close(master_fd)
            os.close(slave_fd)
            self.new_message_signal.emit(f"Failed to start Claude: {e}", False)
            self.set_typing_signal.emit(False)
            self._responding = False
            return

        self._master_fd = master_fd
        reader = threading.Thread(target=self._read_events, daemon=True)
        reader.start()

    def _read_events(self):
        import select as select_mod

        should_reload = False
        buf = ""

        while self.claude_proc and self.claude_proc.poll() is None:
            try:
                r, _, _ = select_mod.select([self._master_fd], [], [], 0.5)
                if not r:
                    continue
                data = os.read(self._master_fd, 65536)
                if not data:
                    break
            except OSError:
                break

            buf += data.decode("utf-8", errors="replace")

            # Process complete JSON lines
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if "freecadcmd" in line or ".FCStd" in line:
                    should_reload = True

                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                etype = event.get("type", "")

                # Streaming text from Claude
                if etype == "stream_event":
                    inner = event.get("event", {})
                    inner_type = inner.get("type", "")

                    if inner_type == "content_block_start":
                        block = inner.get("content_block", {})
                        if block.get("type") == "text":
                            self.clear_status_signal.emit()
                            self.reset_assistant_signal.emit()
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            label = self._tool_label(tool_name)
                            if label:
                                self._emit_status(label)

                    elif inner_type == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            chunk = delta.get("text", "")
                            if chunk:
                                self.append_message_signal.emit(chunk)

                # Turn complete — unlock input
                elif etype == "result":
                    if event.get("is_error"):
                        self.new_message_signal.emit(
                            f"Error: {event.get('result', 'Unknown error')}", False)

                    if should_reload:
                        self.reload_doc_signal.emit()
                        should_reload = False

                    self.set_typing_signal.emit(False)
                    self._responding = False

        # Process exited unexpectedly
        if self._responding:
            self.set_typing_signal.emit(False)
            self._responding = False

    # ---- Document management ----

    def _reload_document(self):
        doc = FreeCAD.ActiveDocument
        path = doc.FileName if doc else None

        # If no doc open, try to find the project's FCStd
        if not path and self.project_dir:
            for f in os.listdir(self.project_dir):
                if f.endswith(".FCStd") and not f.endswith(".FCBak"):
                    path = os.path.join(self.project_dir, f)
                    break

        if not path:
            return

        # Close existing doc if it's the same file
        if doc and doc.FileName == path:
            FreeCAD.closeDocument(doc.Name)

        doc = FreeCAD.openDocument(path)
        Gui.activeDocument().activeView().viewIsometric()
        Gui.SendMsgToActiveView("ViewFit")
        obj_count = len(doc.Objects) if doc else 0
        self.new_message_signal.emit(
            f"Document loaded — {obj_count} object{'s' if obj_count != 1 else ''}.", False)
        # Force chat panel repaint after doc reload — FreeCAD's UI rebuild
        # can sometimes blank the scroll area contents
        QtCore.QTimer.singleShot(200, self._force_repaint)

    def _force_repaint(self):
        self.messages_widget.adjustSize()
        self.scroll_area.viewport().update()
        self._scroll_to_bottom()

    def closeEvent(self, event):
        if self.claude_proc and self.claude_proc.poll() is None:
            self.claude_proc.stdin.close()
            self.claude_proc.terminate()
            self.claude_proc = None
        if hasattr(self, '_master_fd'):
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        super().closeEvent(event)


def show_chat_panel():
    global _panel_instance
    mw = Gui.getMainWindow()
    if _panel_instance is None or not _panel_instance.isVisible():
        _panel_instance = VisionCADPanel()
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, _panel_instance)
    else:
        _panel_instance.show()
        _panel_instance.raise_()


def load_reference_image():
    show_chat_panel()
    if _panel_instance:
        _panel_instance._load_image()
