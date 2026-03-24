"""Vision CAD commands — toolbar buttons."""

import FreeCAD
import FreeCADGui as Gui


class OpenChatCommand:
    """Open the Vision CAD chat panel."""

    def GetResources(self):
        return {
            "MenuText": "AI Chat",
            "ToolTip": "Open the Vision CAD AI assistant",
        }

    def Activated(self):
        import vcad_panel
        vcad_panel.show_chat_panel()

    def IsActive(self):
        return True


class LoadImageCommand:
    """Load a reference image to start a new design."""

    def GetResources(self):
        return {
            "MenuText": "Load Reference Image",
            "ToolTip": "Load a photo or sketch of furniture to model",
        }

    def Activated(self):
        import vcad_panel
        vcad_panel.load_reference_image()

    def IsActive(self):
        return True


Gui.addCommand("VisionCAD_OpenChat", OpenChatCommand())
Gui.addCommand("VisionCAD_LoadImage", LoadImageCommand())
