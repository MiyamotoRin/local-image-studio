"""Paged saved-image browser. Image decoding runs outside Tk's UI thread."""
import datetime as dt
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk
from image_storage import read_settings
from image_upscale import upscaler_for
from window_layout import fit_window_to_work_area

BG, PANEL, TEXT, MUTED = '#101820', '#192530', '#edf3f7', '#9caebb'
PAGE_SIZE = 12


def saved_images(directory):
    """List complete images, newest first; ignore atomic-save intermediates."""
    records = []
    for path in Path(directory).iterdir():
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg') or path.stem.endswith('.saving'):
            continue
        try:
            if path.is_file():
                records.append((path.stat().st_mtime_ns, path.name, path))
        except OSError:
            continue  # A file may be moved while the directory is scanned.
    return [item[2] for item in sorted(records, reverse=True)]


class ImageGallery:
    def __init__(self, app, directory):
        self.app = app
        self.directory = Path(directory)
        self.paths = []
        self.page = 0
        self.revision = 0
        self.results = queue.Queue()
        self.photos = []
        self.viewer = None
        self.selected = None
        self.original = None
        self.closed = threading.Event()
        self.window = tk.Toplevel(app.root)
        self.window.title('保存画像 · Local Image Studio')
        self.window.geometry('1080x820')
        self.window.minsize(900, 740)
        self.window.configure(bg=BG)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        toolbar = ttk.Frame(self.window, padding=(20, 16))
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='保存画像', font=('Yu Gothic UI', 18, 'bold')).pack(side='left')
        ttk.Button(toolbar, text='更新', command=self.refresh).pack(side='right')
        ttk.Label(self.window, text='新しい順 · クリックで拡大、生成設定を確認できます',
                  style='Muted.TLabel', padding=(20, 0, 20, 12)).pack(anchor='w')
        self.grid = ttk.Frame(self.window, padding=(20, 0))
        self.grid.pack(fill='both', expand=True)
        for column in range(4):
            self.grid.columnconfigure(column, weight=1, uniform='gallery')
        for row in range(3):
            self.grid.rowconfigure(row, weight=1, uniform='gallery')
        footer = ttk.Frame(self.window, padding=(20, 12, 20, 16))
        footer.pack(fill='x')
        self.previous = ttk.Button(footer, text='← 前のページ', command=lambda: self.turn_page(-1))
        self.previous.pack(side='left')
        self.page_label = tk.StringVar()
        ttk.Label(footer, textvariable=self.page_label, anchor='center').pack(side='left', expand=True)
        self.next = ttk.Button(footer, text='次のページ →', command=lambda: self.turn_page(1))
        self.next.pack(side='right')
        self.refresh()
        self.poll_job = self.window.after(50, self.poll)
        self.window.after_idle(lambda: fit_window_to_work_area(self.window))

    def refresh(self):
        try:
            self.paths = saved_images(self.directory)
            self.scan_error = None
        except OSError as error:
            self.paths = []
            self.scan_error = f'保存先を読み込めません: {error}'
        self.page = min(self.page, max(0, (len(self.paths) - 1) // PAGE_SIZE))
        self.render_page()
        if self.viewer is not None:
            self.update_navigation()

    def turn_page(self, step):
        self.page = max(0, min(self.page + step, max(0, (len(self.paths) - 1) // PAGE_SIZE)))
        self.render_page()

    def render_page(self):
        self.revision += 1
        revision = self.revision
        for widget in self.grid.winfo_children():
            widget.destroy()
        self.photos = []
        self.tiles = {}
        total = len(self.paths)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_label.set(f'{total}枚  ·  {self.page + 1} / {pages}ページ')
        self.previous.configure(state='normal' if self.page > 0 else 'disabled')
        self.next.configure(state='normal' if self.page + 1 < pages else 'disabled')
        if not total:
            ttk.Label(self.grid, text=self.scan_error or '保存画像はまだありません。\n画像を生成するとここに表示されます。',
                      anchor='center', justify='center', wraplength=700).grid(
                          row=0, column=0, columnspan=4, rowspan=3, sticky='nsew')
            return
        batch = self.paths[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        for index, path in enumerate(batch):
            tile = ttk.Frame(self.grid)
            tile.grid(row=index // 4, column=index % 4, sticky='nsew', padx=5, pady=5)
            button = tk.Button(tile, text='読み込み中…', bg=PANEL, fg=TEXT,
                               activebackground='#2c4352', activeforeground=TEXT,
                               relief='flat', borderwidth=0, cursor='hand2',
                               command=lambda p=path: self.open_viewer(p))
            button.pack(fill='both', expand=True)
            ttk.Label(tile, text=path.name, style='Muted.TLabel', anchor='center',
                      font=('Yu Gothic UI', 8), wraplength=210).pack(fill='x', pady=(4, 0))
            self.tiles[path] = button
        # Cancel obsolete pages between files, keeping only one page of thumbnails.
        threading.Thread(target=self.load_thumbnails, args=(revision, batch), daemon=True).start()

    def load_thumbnails(self, revision, paths):
        for path in paths:
            if self.closed.is_set() or revision != self.revision:
                return
            try:
                with Image.open(path) as source:
                    source.thumbnail((210, 140), Image.Resampling.LANCZOS)
                    thumbnail = ImageOps.exif_transpose(source).convert('RGB')
                self.results.put((revision, path, thumbnail))
            except (OSError, ValueError, Image.DecompressionBombError):
                self.results.put((revision, path, None))

    def poll(self):
        while True:
            try:
                revision, path, thumbnail = self.results.get_nowait()
            except queue.Empty:
                break
            if revision != self.revision:
                continue
            button = self.tiles.get(path)
            if button is not None:
                if thumbnail is None:
                    button.configure(text='画像を読み込めません')
                else:
                    photo = ImageTk.PhotoImage(thumbnail, master=self.window)
                    self.photos.append(photo)
                    button.configure(image=photo, text='')
        if self.viewer is not None:
            self.reuse.configure(state='normal' if self.has_settings and not self.app.busy else 'disabled')
            self.upscale.configure(state='normal' if self.can_upscale and not self.app.busy else 'disabled')
        self.poll_job = self.window.after(50, self.poll)

    def open_viewer(self, path):
        if self.viewer is None:
            self.viewer = tk.Toplevel(self.window)
            self.viewer.title('画像を見る · Local Image Studio')
            self.viewer.geometry('1100x780')
            self.viewer.minsize(860, 600)
            self.viewer.configure(bg=BG)
            self.viewer.protocol('WM_DELETE_WINDOW', self.close_viewer)
            self.viewer.bind('<Left>', lambda event: self.navigate(-1))
            self.viewer.bind('<Right>', lambda event: self.navigate(1))
            self.viewer.bind('<Escape>', lambda event: self.close_viewer())
            body = ttk.Frame(self.viewer, padding=16)
            body.pack(fill='both', expand=True)
            body.columnconfigure(0, weight=1)
            body.rowconfigure(0, weight=1)
            self.canvas = tk.Canvas(body, bg='#0c1218', highlightthickness=0)
            self.canvas.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
            self.canvas.bind('<Configure>', lambda event: self.draw_image())
            details = ttk.Frame(body, width=300)
            details.grid(row=0, column=1, sticky='nsew')
            details.grid_propagate(False)
            details.columnconfigure(0, weight=1)
            details.rowconfigure(1, weight=1)
            ttk.Label(details, text='画像と生成設定', font=('Yu Gothic UI', 13, 'bold')).grid(
                row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
            self.details = tk.Text(details, wrap='word', bg=PANEL, fg=TEXT, relief='flat',
                                   font=('Yu Gothic UI', 10), padx=12, pady=12, width=26,
                                   state='disabled', exportselection=False)
            self.details.grid(row=1, column=0, sticky='nsew')
            scrollbar = ttk.Scrollbar(details, command=self.details.yview)
            scrollbar.grid(row=1, column=1, sticky='ns')
            self.details.configure(yscrollcommand=scrollbar.set)
            self.reuse = ttk.Button(details, text='この設定で生成する', style='Accent.TButton',
                                    command=self.reuse_settings)
            self.reuse.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(12, 0))
            self.external = ttk.Button(details, text='画像を外部アプリで開く', command=self.open_external)
            self.external.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
            self.upscale = ttk.Button(details, text='2倍に高精細化（別画像で保存）',
                                      command=self.upscale_selected)
            self.upscale.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(8, 0))
            self.upscale_hint = tk.StringVar()
            ttk.Label(details, textvariable=self.upscale_hint, style='Muted.TLabel',
                      wraplength=290).grid(row=5, column=0, columnspan=2, sticky='w', pady=(8, 0))
            navigation = ttk.Frame(self.viewer, padding=(16, 0, 16, 16))
            navigation.pack(fill='x')
            self.prev_image = ttk.Button(navigation, text='← 前の画像', command=lambda: self.navigate(-1))
            self.prev_image.pack(side='left')
            self.position = tk.StringVar()
            ttk.Label(navigation, textvariable=self.position, anchor='center').pack(side='left', expand=True)
            self.next_image = ttk.Button(navigation, text='次の画像 →', command=lambda: self.navigate(1))
            self.next_image.pack(side='right')
            self.viewer.after_idle(lambda: fit_window_to_work_area(self.viewer) if self.viewer is not None else None)
        self.selected = Path(path)
        self.original = None
        self.has_settings = False
        self.can_upscale = False
        self.upscale_hint.set('生成モデルを判別できる画像を選んでください。')
        details = [self.selected.name]
        try:
            with Image.open(path) as source:
                self.original = ImageOps.exif_transpose(source).copy()
            stamp = dt.datetime.fromtimestamp(self.selected.stat().st_mtime)
            details.extend([f'\n保存日時: {stamp:%Y/%m/%d %H:%M:%S}',
                            f'画像サイズ: {self.original.width} × {self.original.height}'])
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            details.append(f'\n画像を読み込めません:\n{error}')
        if self.original is not None:
            try:
                settings = read_settings(path)
                if not isinstance(settings, dict):
                    raise ValueError('生成設定の形式が正しくありません。')
                self.has_settings = True
                try:
                    scaler = upscaler_for(settings)
                    self.can_upscale = True
                    self.upscale_hint.set('使用モデル: ' + scaler.removesuffix('.pth') + '\n処理の中止はメイン画面の「中止」から。')
                except ValueError as error:
                    self.upscale_hint.set(str(error))
                model = settings.get('model') or settings.get('model_id', '不明')
                details.extend([f'\nモデル: {model}', f"ステップ: {settings.get('steps', '不明')}",
                                f"seed: {settings.get('seed', '不明')}",
                                f"CFG: {settings.get('cfg', '不明')}",
                                '\nプロンプト\n' + str(settings.get('prompt', '記録なし'))])
                if settings.get('negative_prompt'):
                    details.append('\nネガティブプロンプト\n' + str(settings['negative_prompt']))
                if settings.get('loras'):
                    details.append('\nLoRA\n' + '\n'.join(
                        f"{item['file']} × {item['strength']}" for item in settings['loras']))
                if settings.get('import_warnings'):
                    details.append('\n' + str(settings['import_warnings']))
                if settings.get('upscale'):
                    up = settings['upscale']
                    details.extend(['\n高精細化: 2倍 / ' + str(up.get('model', '不明')),
                                    '元画像: ' + str(up.get('source_file', '不明')),
                                    f"生成時のサイズ: {settings.get('width')} × {settings.get('height')}"])
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                details.append(f'\n生成設定を読み込めません:\n{error}')
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', '\n'.join(details))
        self.details.configure(state='disabled')
        self.reuse.configure(state='normal' if self.has_settings and not self.app.busy else 'disabled')
        self.external.configure(state='normal' if self.original is not None else 'disabled')
        self.upscale.configure(state='normal' if self.can_upscale and not self.app.busy else 'disabled')
        self.update_navigation()
        self.draw_image()
        self.viewer.lift()

    def update_navigation(self):
        index = self.paths.index(self.selected) if self.selected in self.paths else -1
        self.position.set(f'{index + 1} / {len(self.paths)}  ·  ← → キーで移動' if index >= 0 else '一覧にない画像です。更新してください。')
        self.prev_image.configure(state='normal' if index > 0 else 'disabled')
        self.next_image.configure(state='normal' if 0 <= index < len(self.paths) - 1 else 'disabled')

    def navigate(self, step):
        if self.selected in self.paths:
            index = self.paths.index(self.selected) + step
            if 0 <= index < len(self.paths):
                self.open_viewer(self.paths[index])

    def draw_image(self):
        self.canvas.delete('all')
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.original is None:
            self.canvas.create_text(width / 2, height / 2, text='画像を読み込めません', fill=MUTED)
            return
        preview = ImageOps.contain(self.original, (max(1, width - 24), max(1, height - 24)),
                                   Image.Resampling.LANCZOS)
        self.viewer_photo = ImageTk.PhotoImage(preview, master=self.viewer)
        self.canvas.create_image(width / 2, height / 2, image=self.viewer_photo)

    def reuse_settings(self):
        if not self.app.busy and self.selected and self.app.restore_settings(self.selected):
            self.close_viewer()
            self.window.withdraw()
            self.app.root.lift()
            self.app.root.focus_set()

    def open_external(self):
        try:
            os.startfile(str(self.selected))
        except OSError as error:
            from tkinter import messagebox
            messagebox.showerror('画像を開けません', str(error), parent=self.viewer)

    def upscale_selected(self):
        if self.can_upscale and not self.app.busy and self.selected:
            if self.app.start_upscale(self.selected):
                self.upscale.configure(state='disabled')

    def close_viewer(self):
        self.viewer.destroy()
        self.viewer = None
        self.original = None
        self.viewer_photo = None

    def close(self):
        self.closed.set()
        self.window.after_cancel(self.poll_job)
        self.window.destroy()
        self.app.gallery = None
