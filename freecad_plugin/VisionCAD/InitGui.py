"""Vision CAD — FreeCAD Workbench with AI-powered furniture design."""

import os
import sys
import FreeCAD

# Add our module dir to path
_mod_dir = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "VisionCAD")
if _mod_dir not in sys.path:
    sys.path.insert(0, _mod_dir)


class VisionCADWorkbench(Workbench):
    """Vision CAD workbench — AI-powered furniture design."""
    MenuText = "Vision CAD"
    ToolTip = "AI-powered furniture design from reference images"

    def Initialize(self):
        import vcad_commands
        self.appendToolbar("Vision CAD", ["VisionCAD_OpenChat", "VisionCAD_LoadImage"])
        self.appendMenu("Vision CAD", ["VisionCAD_OpenChat", "VisionCAD_LoadImage"])

    def Activated(self):
        import vcad_panel
        vcad_panel.show_chat_panel()

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(VisionCADWorkbench())
