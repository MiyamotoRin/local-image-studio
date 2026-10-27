"""Regression check for disappearing prompt fields on laptop-size windows."""
import tkinter as tk
import unittest
from types import SimpleNamespace

from image_studio import App, MODELS


class SettingsLayoutTests(unittest.TestCase):
    def test_form_scrolls_while_editor_height_and_actions_remain_stable(self):
        root=tk.Tk()
        try:
            app=App(root)
            app.model_label.set(MODELS['z-image-base']['label']); app.change_model()
            for width,height in ((1160,720),(1160,820)):
                root.geometry(f'{width}x{height}')
                root.update()
                panel=app.settings_panel
                self.assertGreaterEqual(app.prompt.winfo_height(),160)
                self.assertGreater(panel.canvas.winfo_height(),150)
                self.assertLess(panel.canvas.yview()[1]-panel.canvas.yview()[0],1)
                button_y=app.generate.winfo_rooty()
                panel.on_wheel(SimpleNamespace(widget=app.cfg_entry,delta=-120))
                root.update()
                self.assertGreater(panel.canvas.yview()[0],0)
                panel.canvas.yview_moveto(1)
                root.update()
                self.assertEqual(app.generate.winfo_rooty(),button_y)
                self.assertLess(app.progress.winfo_rooty()+app.progress.winfo_height(),root.winfo_rooty()+root.winfo_height())
                panel.canvas.yview_moveto(0)
                root.update()
                # Keyboard navigation also reveals fields below the viewport.
                hidden=app.seed_entry.winfo_rooty()+app.seed_entry.winfo_height()>panel.canvas.winfo_rooty()+panel.canvas.winfo_height()
                panel.on_focus(SimpleNamespace(widget=app.seed_entry))
                root.update()
                if hidden: self.assertGreater(panel.canvas.yview()[0],0)
                self.assertGreaterEqual(app.seed_entry.winfo_rooty(),panel.canvas.winfo_rooty())
                panel.canvas.yview_moveto(0)
        finally:
            for job in root.tk.call('after','info'): root.after_cancel(job)
            root.destroy()


if __name__=='__main__':
    unittest.main()
