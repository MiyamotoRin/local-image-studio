"""Local desktop UI for the verified stable-diffusion.cpp installation."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk
from image_storage import read_settings, finish_image
from image_gallery import ImageGallery
from image_upscale import prepare_upscale, upscale_image
from window_layout import fit_window_to_work_area
from generation_options import conditioning, engine_prompt, LORA_DIR
from scrollable_settings import ScrollableSettings
from app_config import load_config

ROOT = Path(__file__).resolve().parent
CONFIG = load_config(ROOT)
MODELS_DIR = CONFIG['models_dir']
OUTPUT_DIR = CONFIG['output_dir']
DATA_DIR = CONFIG['data_dir']
ENGINE = CONFIG['engine_path']
if os.name == 'nt':
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LocalImageStudio.Desktop')
BG, PANEL, TEXT, MUTED, ACCENT = '#101820', '#192530', '#edf3f7', '#9caebb', '#63d5ba'
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

# Exact aspect ratios with dimensions divisible by 16.
SIZE_PRESETS = {
    '1:1 正方形': ((512,512), (1024,1024), (1280,1280)),
    '3:2 横長': ((768,512), (1248,832), (1584,1056)),
    '2:3 縦長': ((512,768), (832,1248), (1056,1584)),
    '4:3 横長': ((640,480), (1152,864), (1472,1104)),
    '3:4 縦長': ((480,640), (864,1152), (1104,1472)),
    '16:9 ワイド': ((768,432), (1280,720), (1792,1008)),
    '9:16 縦ワイド': ((432,768), (720,1280), (1008,1792)),
    '4:5 縦長': ((512,640), (896,1120), (1152,1440)),
    '5:4 横長': ((640,512), (1120,896), (1440,1152)),
}
SIZE_LEVELS = ('軽量', '高解像度（約100万画素）', '超高解像度（約160万画素）')


MODELS = {
    # Q4_0 CUDA matmuls produced all-white images on this RTX 3050. Convert the
    # affected transformer weights to Q5_0 in memory; retain the downloaded file.
    'z-image-base': dict(label='Z-Image Base · Q4_0', steps=28, cfg=4.0, diffusion='z-image-Q4_0.gguf', llm='Qwen3-4B-Instruct-2507-Q4_K_M.gguf', vae='ae.safetensors', extra_args=['--sampling-method','euler','--batched-cfg','off','--tensor-type-rules',r'.*(feed_forward|attention|adaLN_modulation).*weight=q5_0']),
    'anima-turbo': dict(label='Anima Turbo 1.1 · Q4_K_M', steps=8, diffusion='anima-turbo-v1.1-Q4_K_M.gguf', llm='qwen_3_06b_base.safetensors', vae='qwen_image_vae.safetensors', extra_args=['--sampling-method','euler']),
    'z-image-turbo': dict(label='Z-Image Turbo · Q3_K', steps=8, diffusion='z_image_turbo-Q3_K.gguf', llm='Qwen3-4B-Instruct-2507-Q4_K_M.gguf', vae='ae.safetensors'),
    'flux2-klein-4b': dict(label='FLUX.2 klein 4B · Q4_0', steps=4, diffusion='flux-2-klein-4b-Q4_0.gguf', llm='Qwen3-4B-Q4_K_M.gguf', vae='flux2-vae.safetensors'),
}

def command(settings, output):
    model = MODELS[settings.get("model_id", "z-image-turbo")]
    options = conditioning(settings, check_files=True)
    return [str(ENGINE),
            '--diffusion-model', str(MODELS_DIR/model['diffusion']),
            '--llm', str(MODELS_DIR/model['llm']),
            '--vae', str(MODELS_DIR/model['vae']),
            '--offload-to-cpu', '--max-vram', str(CONFIG['max_vram']), '--cfg-scale', str(options['cfg']),
            '--steps', str(settings['steps']), '-W', str(settings['width']),
            '-H', str(settings['height']), '--seed', str(settings['seed']),
            '-p', engine_prompt(settings), '-o', str(output)] + model.get('extra_args', []) + (
                ['-n', options['negative_prompt']] if settings.get('model_id') == 'z-image-base' else []) + (
                ['--lora-model-dir', str(LORA_DIR), '--lora-apply-mode', 'at_runtime'] if options['loras'] else [])

class App:
    def __init__(self, root):
        for folder in (OUTPUT_DIR, DATA_DIR, DATA_DIR/'logs'):
            folder.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.last_image = None
        self.original = None
        self.photo = None
        self.started = 0
        self.batch_total = 1
        self.batch_index = 1
        self.completed = 0
        self.phase = '準備完了'
        self.resize_job = None
        self.gallery = None
        self.negative_prompt = ''
        self.loras = []
        self.conditioning_window = None
        self._conditioning_model = None
        self._conditioning_by_model = {}
        root.title('Local Image Studio 2.0.0')
        root.iconbitmap(default=str(ROOT/'assets/studio.ico'))
        root.geometry('1160x820')
        root.minsize(980, 720)
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Yu Gothic UI', 10), background=BG, foreground=TEXT)
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=TEXT)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('Title.TLabel', font=('Yu Gothic UI', 22, 'bold'))
        style.configure('TButton', padding=(12, 8), background=PANEL, foreground=TEXT, borderwidth=0)
        style.map('TButton', background=[('active', '#2c4352')], foreground=[('disabled', '#60717e')])
        style.configure('Accent.TButton', background=ACCENT, foreground='#10251f', font=('Yu Gothic UI', 11, 'bold'))
        style.map('Accent.TButton', background=[('active', '#87e6cf'), ('disabled', '#344c47')])
        style.configure('TEntry', fieldbackground=PANEL, foreground=TEXT, insertcolor=TEXT, padding=6)
        style.configure('TSpinbox', fieldbackground=PANEL, foreground=TEXT, arrowcolor=TEXT, padding=6)
        style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=PANEL, borderwidth=0)
        head = ttk.Frame(root, padding=(28, 16, 28, 12)); head.pack(fill='x')
        ttk.Label(head, text='Local Image Studio', style='Title.TLabel').pack(side='left')
        self.model_label=tk.StringVar(value=MODELS['z-image-turbo']['label'])
        self.model_box=ttk.Combobox(head,textvariable=self.model_label,values=[m['label'] for m in MODELS.values()],state='readonly',width=29)
        self.model_box.pack(side='right')
        ttk.Label(head,text='生成モデル  ',style='Muted.TLabel').pack(side='right')
        self.model_box.bind('<<ComboboxSelected>>',self.change_model)
        body = ttk.Frame(root, padding=(28, 0, 28, 16)); body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1); body.rowconfigure(0, weight=1)
        left_shell=ttk.Frame(body,width=400)
        left_shell.grid(row=0,column=0,sticky='nsew',padx=(0,24))
        left_shell.grid_propagate(False); left_shell.columnconfigure(0,weight=1); left_shell.rowconfigure(0,weight=1)
        self.settings_panel=ScrollableSettings(left_shell,BG)
        self.settings_panel.grid(row=0,column=0,sticky='nsew')
        left=self.settings_panel.content
        left.columnconfigure(0,weight=1)
        ttk.Label(left, text='作りたい画像', font=('Yu Gothic UI', 13, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(left, text='被写体・光・場所を具体的に。英語がおすすめです。', style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(4, 10))
        self.prompt = tk.Text(left, height=9, wrap='word', bg=PANEL, fg=TEXT, insertbackground=TEXT,
                              relief='flat', padx=12, pady=12, font=('Yu Gothic UI', 11), undo=True)
        self.prompt.grid(row=2, column=0, sticky='ew')
        self.prompt.insert('1.0', (DATA_DIR/'prompt.txt').read_text(encoding='utf-8-sig') if (DATA_DIR/'prompt.txt').exists() else '')
        params = ttk.Frame(left); params.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        self.width, self.height, self.steps, self.seed = [tk.StringVar(value=v) for v in ('512', '512', '8', '-1')]
        self.count = tk.StringVar(value='1')
        self.cfg = tk.StringVar(value='1.0')
        self.output_format=tk.StringVar(value='PNG')
        self.jpeg_quality=tk.StringVar(value='95')
        presets = ttk.Frame(params); presets.pack(fill='x', pady=(0, 10))
        self.ratio = tk.StringVar(value='1:1 正方形')
        self.size_level = tk.StringVar(value=SIZE_LEVELS[0])
        self._setting_preset = False
        style.configure('TCombobox', fieldbackground=PANEL, foreground=TEXT, arrowcolor=TEXT, padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', TEXT)], selectbackground=[('readonly', PANEL)], selectforeground=[('readonly', TEXT)])
        root.option_add('*TCombobox*Listbox.background', PANEL)
        root.option_add('*TCombobox*Listbox.foreground', TEXT)
        presets.columnconfigure(0, weight=1); presets.columnconfigure(1, weight=1)
        ttk.Label(presets,text='画像の比率').grid(row=0,column=0,sticky='w',pady=(0,4))
        ttk.Label(presets,text='サイズ').grid(row=0,column=1,sticky='w',pady=(0,4))
        self.ratio_box = ttk.Combobox(presets,textvariable=self.ratio,values=tuple(SIZE_PRESETS)+('カスタム',),state='readonly',width=15)
        self.ratio_box.grid(row=1,column=0,sticky='ew',padx=(0,8))
        self.level_box = ttk.Combobox(presets,textvariable=self.size_level,values=SIZE_LEVELS,state='readonly',width=22)
        self.level_box.grid(row=1,column=1,sticky='ew')
        self.ratio_box.bind('<<ComboboxSelected>>',self.apply_size_preset)
        self.level_box.bind('<<ComboboxSelected>>',self.apply_size_preset)
        self.width.trace_add('write',self.sync_size_preset)
        self.height.trace_add('write',self.sync_size_preset)
        fields = ttk.Frame(params); fields.pack(fill='x')
        self.inputs = []
        for col,(label,var,low,high,inc) in enumerate([('横幅',self.width,256,2048,16),('高さ',self.height,256,2048,16),('ステップ',self.steps,1,50,1),('枚数',self.count,1,100,1)]):
            fields.columnconfigure(col,weight=1)
            ttk.Label(fields,text=label).grid(row=0,column=col,sticky='w',pady=(0,4))
            entry=ttk.Spinbox(fields,textvariable=var,from_=low,to=high,increment=inc,width=6)
            entry.grid(row=1,column=col,sticky='ew',padx=(0,8 if col<3 else 0)); self.inputs.append(entry)
        cfgrow=ttk.Frame(params); cfgrow.pack(fill='x',pady=(10,0))
        ttk.Label(cfgrow,text='CFG').pack(side='left')
        self.cfg_entry=ttk.Spinbox(cfgrow,textvariable=self.cfg,from_=0,to=20,increment=0.5,width=6)
        self.cfg_entry.pack(side='left',padx=(8,12))
        self.conditioning_button=ttk.Button(cfgrow,text='ネガティブ・LoRA…',command=self.edit_conditioning)
        self.conditioning_button.pack(side='right')
        self.conditioning_summary=tk.StringVar()
        ttk.Label(params,textvariable=self.conditioning_summary,style='Muted.TLabel',wraplength=375).pack(anchor='w',pady=(4,0))
        ttk.Label(params,text='seed  /  -1: 毎枚ランダム・指定値: 1ずつ加算').pack(anchor='w',pady=(8,4))
        seedrow=ttk.Frame(params); seedrow.pack(fill='x')
        self.seed_entry=ttk.Entry(seedrow,textvariable=self.seed); self.seed_entry.pack(side='left',fill='x',expand=True)
        ttk.Button(seedrow,text='ランダム',command=lambda:self.seed.set('-1')).pack(side='left',padx=(8,0))
        formats=ttk.Frame(params); formats.pack(fill='x',pady=(10,0))
        ttk.Label(formats,text='保存形式').pack(side='left')
        ttk.Combobox(formats,textvariable=self.output_format,values=('PNG','JPEG'),state='readonly',width=7).pack(side='left',padx=(8,16))
        self.jpeg_controls=ttk.Frame(formats)
        ttk.Label(self.jpeg_controls,text='JPEG品質').pack(side='left')
        ttk.Combobox(self.jpeg_controls,textvariable=self.jpeg_quality,values=('80','85','90','95','100'),state='readonly',width=5).pack(side='left',padx=(8,0))
        self.output_format.trace_add('write',self.update_format_controls)
        self.update_format_controls()
        self.model_tip=tk.StringVar()
        ttk.Label(params,textvariable=self.model_tip,style='Muted.TLabel',wraplength=375).pack(anchor='w',pady=(6,6))
        self.settings_panel.bind_form()
        actions=ttk.Frame(left_shell); actions.grid(row=1,column=0,sticky='ew',pady=(10,0))
        self.generate=ttk.Button(actions,text='画像を生成',style='Accent.TButton',command=self.start)
        self.generate.pack(side='left',fill='x',expand=True)
        self.stop=ttk.Button(actions,text='中止',command=self.stop_run,state='disabled'); self.stop.pack(side='left',padx=(8,0))
        self.status=tk.StringVar(value='準備完了  •  設定を変更して生成できます')
        ttk.Label(left_shell,textvariable=self.status,style='Muted.TLabel',wraplength=375).grid(row=2,column=0,sticky='w',pady=(12,6))
        self.progress=ttk.Progressbar(left_shell,mode='determinate',maximum=100); self.progress.grid(row=3,column=0,sticky='ew')
        right=ttk.Frame(body); right.grid(row=0,column=1,sticky='nsew'); right.columnconfigure(0,weight=1); right.rowconfigure(1,weight=1)
        bar=ttk.Frame(right); bar.grid(row=0,column=0,sticky='ew',pady=(0,10))
        ttk.Label(bar,text='プレビュー',font=('Yu Gothic UI',13,'bold')).pack(side='left')
        ttk.Button(bar,text='保存先を開く',command=self.open_folder).pack(side='right')
        ttk.Button(bar,text='保存画像を見る',command=self.open_gallery).pack(side='right',padx=(0,8))
        self.canvas=tk.Canvas(right,bg='#0c1218',highlightthickness=1,highlightbackground='#293b48')
        self.canvas.grid(row=1,column=0,sticky='nsew'); self.canvas.bind('<Configure>',self.resize)
        self.info=tk.StringVar(value='完成した画像がここに表示されます')
        ttk.Label(right,textvariable=self.info,style='Muted.TLabel',wraplength=620).grid(row=2,column=0,sticky='w',pady=(10,0))
        image_actions=ttk.Frame(right); image_actions.grid(row=3,column=0,sticky='ew',pady=(8,0))
        self.open_button=ttk.Button(image_actions,text='画像を開く',command=self.open_image,state='disabled'); self.open_button.pack(side='left')
        self.restore_button=ttk.Button(image_actions,text='この画像の設定を戻す',command=self.restore_current); self.restore_button.pack(side='left',padx=(8,0))
        self.load_button=ttk.Button(image_actions,text='保存画像から読み込む',command=self.choose_settings); self.load_button.pack(side='left',padx=(8,0))
        footer=ttk.Frame(root,padding=(28,0,28,12)); footer.pack(fill='x')
        ttk.Label(footer,text='実行ログ',style='Muted.TLabel').pack(anchor='w',pady=(0,5))
        self.log=tk.Text(footer,height=3,bg=PANEL,fg=MUTED,relief='flat',font=('Consolas',9),padx=10,pady=8,state='disabled',wrap='word')
        self.log.pack(fill='x')
        try:
            saved=json.loads((DATA_DIR/'ui-settings.json').read_text(encoding='utf-8'))
            self.count.set(str(saved.get('count',1)))
            self.output_format.set(saved.get('format','PNG'))
            self.jpeg_quality.set(str(saved.get('jpeg_quality',95)))
            self.model_label.set(MODELS.get(saved.get('model_id'),MODELS['z-image-turbo'])['label'])
            options=conditioning(dict(saved, cfg=saved.get('cfg',MODELS[self.model_id()].get('cfg',1.0))))
            self.cfg.set(str(options['cfg']))
            self.negative_prompt=options['negative_prompt']; self.loras=options['loras']
            self._conditioning_model=self.model_id()
            for model_id,values in saved.get('conditioning_by_model',{}).items():
                if model_id in ('z-image-base','z-image-turbo'):
                    self._conditioning_by_model[model_id]=conditioning(dict(values,model_id=model_id))
            for name,var in [('width',self.width),('height',self.height),('steps',self.steps),('seed',self.seed)]: var.set(str(saved[name]))
        except (OSError,ValueError,KeyError): pass
        self.change_model(reset_steps=False)
        images=sorted(p for p in (OUTPUT_DIR).iterdir() if p.suffix.lower() in ('.png','.jpg','.jpeg'))
        if images: self.show_image(images[-1]); self.info.set('前回の画像  •  '+images[-1].name)
        root.after(100,self.poll)
        root.after_idle(lambda: fit_window_to_work_area(root))

    def update_format_controls(self,*args):
        if self.output_format.get()=='JPEG':
            self.jpeg_controls.pack(side='left')
        else:
            self.jpeg_controls.pack_forget()

    def model_id(self):
        return next(k for k,v in MODELS.items() if v['label']==self.model_label.get())

    def change_model(self,event=None,reset_steps=True):
        model_id=self.model_id()
        if self._conditioning_model!=model_id:
            if self._conditioning_model is not None:
                self._conditioning_by_model[self._conditioning_model]=dict(negative_prompt=self.negative_prompt,loras=[dict(item) for item in self.loras])
            previous=self._conditioning_by_model.get(model_id,{})
            self.negative_prompt=previous.get('negative_prompt','')
            self.loras=[dict(item) for item in previous.get('loras',[])]
            self._conditioning_model=model_id
        model=MODELS[model_id]
        if reset_steps:
            self.steps.set(str(model['steps']))
            self.cfg.set(str(model.get('cfg',1.0)))
        base=self.model_id()=='z-image-base'
        self.conditioning_button.configure(state='normal' if model_id in ('z-image-base','z-image-turbo') else 'disabled',text='ネガティブ・LoRA…' if base else 'LoRA…')
        self.update_conditioning_summary()
        self.model_tip.set('Base推奨 28〜50ステップ・CFG 3〜5。\nネガティブはCFG>1で有効。' if base else f"推奨 {model['steps']}ステップ・CFG 1.0。\n大きな画像ほど時間・メモリが必要です。")

    def update_conditioning_summary(self):
        if self.model_id() in ('z-image-base','z-image-turbo'):
            active=sum(item['strength']!=0 for item in self.loras)
            self.conditioning_summary.set((f'ネガティブ: {"あり" if self.negative_prompt.strip() else "なし"}  /  ' if self.model_id()=='z-image-base' else 'Turbo用 ') + f'LoRA: {active}個')
        else:
            self.conditioning_summary.set('LoRA設定はZ-Image Base / Turbo用です。')

    def edit_conditioning(self):
        if self.busy or self.model_id() not in ('z-image-base','z-image-turbo'): return
        if self.conditioning_window is not None and self.conditioning_window.winfo_exists():
            self.conditioning_window.lift(); return
        window=tk.Toplevel(self.root); self.conditioning_window=window
        model_id=self.model_id()
        base=model_id=='z-image-base'
        model_name='Z-Image Base' if base else 'Z-Image Turbo'
        window.title(model_name+' — '+('ネガティブプロンプト・LoRA' if base else 'LoRA'))
        window.configure(bg=BG); window.geometry('680x580'); window.minsize(580,480)
        window.transient(self.root)
        frame=ttk.Frame(window,padding=18); frame.pack(fill='both',expand=True)
        negative=tk.Text(frame,height=5,wrap='word',bg=PANEL,fg=TEXT,insertbackground=TEXT,relief='flat',padx=10,pady=8,undo=True)
        if base:
            ttk.Label(frame,text='ネガティブプロンプト（避けたい要素）').pack(anchor='w')
            negative.pack(fill='both',expand=True,pady=(6,8)); negative.insert('1.0',self.negative_prompt)
            ttk.Label(frame,text='CFGを1より大きくすると有効です。まずはCFG 4.0から。',style='Muted.TLabel').pack(anchor='w')
        else:
            ttk.Label(frame,text='Turbo用LoRAを選んでください。通常は8ステップ・CFG 1.0。',style='Muted.TLabel').pack(anchor='w')
        ttk.Label(frame,text=model_name+'用LoRA（.safetensors）',font=('Yu Gothic UI',11,'bold')).pack(anchor='w',pady=(14,6))
        rows=[dict(item) for item in self.loras]
        listing=tk.Listbox(frame,height=4,bg=PANEL,fg=TEXT,selectbackground='#2c4352',exportselection=False,relief='flat')
        listing.pack(fill='both',expand=True)
        strength=tk.StringVar(value='1.0')
        def refresh():
            listing.delete(0,'end')
            for item in rows: listing.insert('end',f"{item['file']}   × {item['strength']:g}")
        def select(event=None):
            if listing.curselection(): strength.set(str(rows[listing.curselection()[0]]['strength']))
        listing.bind('<<ListboxSelect>>',select)
        def add():
            LORA_DIR.mkdir(parents=True,exist_ok=True)
            paths=filedialog.askopenfilenames(parent=window,title=model_name+'用LoRAを選択',initialdir=str(LORA_DIR),filetypes=[('LoRA','*.safetensors')])
            try:
                for path in paths:
                    source=Path(path); target=LORA_DIR/source.name
                    conditioning(dict(model_id=model_id,loras=[dict(file=source.name,strength=1.0)]))
                    if source.resolve()!=target.resolve():
                        if target.exists(): raise ValueError('同名ファイルが既にあります。名前を変えて追加してください: '+source.name)
                        # Exclusive destination prevents accidental replacement of an installed LoRA.
                        created=False
                        try:
                            with source.open('rb') as src,target.open('xb') as dst:
                                created=True
                                shutil.copyfileobj(src,dst)
                        except OSError:
                            if created and target.exists(): target.unlink()
                            raise
                    if not any(item['file'].casefold()==source.name.casefold() for item in rows):
                        rows.append(dict(file=source.name,strength=1.0))
                refresh()
            except (OSError,ValueError) as error:
                refresh(); messagebox.showerror('LoRAを追加できません',str(error),parent=window)
        def remove():
            for index in reversed(listing.curselection()): del rows[index]
            refresh()
        def set_strength():
            if not listing.curselection(): return
            index=listing.curselection()[0]
            try:
                item=conditioning(dict(model_id=model_id,loras=[dict(file=rows[index]['file'],strength=strength.get())]))['loras'][0]
                rows[index]=item; refresh(); listing.selection_set(index)
            except ValueError as error: messagebox.showerror('LoRA強度を確認してください',str(error),parent=window)
        controls=ttk.Frame(frame); controls.pack(fill='x',pady=(8,6))
        ttk.Button(controls,text='追加…',command=add).pack(side='left')
        ttk.Button(controls,text='外す',command=remove).pack(side='left',padx=(6,12))
        ttk.Label(controls,text='強度').pack(side='left')
        ttk.Spinbox(controls,textvariable=strength,from_=-2,to=2,increment=0.1,width=5).pack(side='left',padx=6)
        ttk.Button(controls,text='強度を反映',command=set_strength).pack(side='left')
        ttk.Label(frame,text='選択したファイルをmodels/lorasへコピーします。強度0で無効。\n対応モデルは配布元で確認してください。内部形式によっては未対応です。\n必要なトリガー語はプロンプトへ。',style='Muted.TLabel',wraplength=620).pack(anchor='w',pady=(4,12))
        def save():
            if self.busy: return
            # Apply any currently edited strength before committing the dialog.
            candidate=[dict(item) for item in rows]
            if listing.curselection():
                index=listing.curselection()[0]
                candidate[index]['strength']=strength.get()
            try:
                result=conditioning(dict(model_id=model_id,negative_prompt=negative.get('1.0','end-1c') if base else '',loras=candidate))
            except ValueError as error:
                messagebox.showerror('設定を確認してください',str(error),parent=window); return
            self.negative_prompt=result['negative_prompt']; self.loras=result['loras']
            self.update_conditioning_summary(); window.destroy()
        buttons=ttk.Frame(frame); buttons.pack(fill='x')
        ttk.Button(buttons,text='キャンセル',command=window.destroy).pack(side='right')
        ttk.Button(buttons,text='設定を使う',style='Accent.TButton',command=save).pack(side='right',padx=8)
        refresh(); fit_window_to_work_area(window)
        window.grab_set()

    def preset(self,w,h):
        self._setting_preset = True
        self.width.set(str(w)); self.height.set(str(h))
        self._setting_preset = False
        self.sync_size_preset()

    def apply_size_preset(self,event=None):
        sizes = SIZE_PRESETS.get(self.ratio.get())
        if sizes:
            self.preset(*sizes[SIZE_LEVELS.index(self.size_level.get())])

    def sync_size_preset(self,*args):
        if self._setting_preset: return
        try: dimensions = (int(self.width.get()),int(self.height.get()))
        except ValueError:
            self.ratio.set('カスタム'); return
        for name,sizes in SIZE_PRESETS.items():
            if dimensions in sizes:
                self.ratio.set(name); self.size_level.set(SIZE_LEVELS[sizes.index(dimensions)])
                return
        self.ratio.set('カスタム')

    def settings(self):
        prompt=self.prompt.get('1.0','end').strip()
        if not prompt: raise ValueError('プロンプトを入力してください。')
        try: w,h,steps,seed,count=map(int,(self.width.get(),self.height.get(),self.steps.get(),self.seed.get(),self.count.get()))
        except ValueError: raise ValueError('解像度・ステップ数・seed・枚数には整数を入力してください。')
        if any(v<256 or v>2048 or v%16 for v in (w,h)): raise ValueError('横幅と高さは256〜2048の、16の倍数にしてください。')
        if not 1<=steps<=50: raise ValueError('ステップ数は1〜50にしてください。モデルごとの推奨値を参考にしてください。')
        if not -1<=seed<=4294967295: raise ValueError('seedは-1〜4294967295にしてください。')
        if not 1<=count<=100: raise ValueError('枚数は1〜100にしてください。')
        if self.output_format.get() not in ('PNG','JPEG'): raise ValueError('保存形式はPNGまたはJPEGを選んでください。')
        quality=int(self.jpeg_quality.get())
        if not 80<=quality<=100: raise ValueError('JPEG品質は80〜100にしてください。')
        options=conditioning(dict(model_id=self.model_id(),cfg=self.cfg.get(),
                                  negative_prompt=self.negative_prompt if self.model_id()=='z-image-base' else '',
                                  loras=self.loras if self.model_id() in ('z-image-base','z-image-turbo') else []),check_files=True)
        if self.model_id()=='z-image-base' and options['cfg']<=1 and options['negative_prompt'].strip():
            raise ValueError('ネガティブプロンプトを使う場合はCFGを1より大きくしてください。')
        return dict(prompt=prompt,width=w,height=h,steps=steps,seed=seed,model_id=self.model_id(),count=count,format=self.output_format.get(),jpeg_quality=quality,**options)

    def start(self):
        if self.busy: return
        try:
            settings=self.settings()
            model=MODELS[settings['model_id']]
            for file in [ENGINE]+[MODELS_DIR/model[k] for k in ('diffusion','llm','vae')]:
                if not file.is_file(): raise ValueError('必要なファイルがありません: '+str(file) + ('\nSetup-Z-Image-Base.cmdでBaseモデルを取得してください。' if settings['model_id']=='z-image-base' else ''))
            (DATA_DIR/'prompt.txt').write_text(settings['prompt']+'\n',encoding='utf-8')
            self._conditioning_by_model[self.model_id()]=dict(negative_prompt=self.negative_prompt,loras=self.loras)
            saved={k:v for k,v in settings.items() if k!='prompt'}
            saved['conditioning_by_model']=self._conditioning_by_model
            (DATA_DIR/'ui-settings.json').write_text(json.dumps(saved,indent=2),encoding='utf-8')
        except (ValueError,OSError) as e: messagebox.showerror('設定を確認してください',str(e)); return
        stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output=OUTPUT_DIR/f'image-{stamp}.png'
        logfile=DATA_DIR/'logs'/f'ui-{stamp}.log'
        self.batch_total=settings['count']; self.batch_index=1; self.completed=0
        self.restore_button.configure(state='disabled'); self.load_button.configure(state='disabled')
        self.model_box.configure(state='disabled')
        self.busy=True; self.cancel.clear(); self.started=time.monotonic(); self.phase='モデルを読み込み中'
        self.generate.configure(state='disabled'); self.stop.configure(state='normal'); self.progress['value']=0
        self.append_log(f"生成開始: {settings['count']}枚 / {settings['width']}×{settings['height']} / {settings['steps']} steps / CFG {settings['cfg']} / seed {settings['seed']}")
        self.info.set('生成中。前回の画像は完了まで表示されます。')
        threading.Thread(target=self.worker,args=(settings,output,logfile),daemon=True).start()

    def worker(self,settings,output,logfile):
        try:
            # One subprocess at a time keeps GPU use independent of image count.
            random_seeds = secrets.SystemRandom().sample(range(4294967296),settings['count']) if settings['seed']==-1 else None
            for index in range(settings['count']):
                if self.cancel.is_set():
                    self.events.put(('cancelled',str(logfile))); return
                seed=random_seeds[index] if random_seeds is not None else (settings['seed']+index)%4294967296
                item=dict(settings,seed=seed,batch_index=index+1)
                suffix=f'-{index+1:03d}' if settings['count']>1 else ''
                destination=output.with_name(output.stem+suffix+output.suffix)
                item_log=logfile.with_name(logfile.stem+suffix+logfile.suffix)
                self.events.put(('item_start',index+1))
                if not self.run_one(item,destination,item_log): return
            self.events.put(('batch_done',time.monotonic()-self.started))
        except Exception as e:
            self.events.put(('error',str(e)))

    def start_upscale(self, path):
        if self.busy: return False
        try:
            settings, model, size = prepare_upscale(path)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            messagebox.showerror('高精細化できません',str(error),parent=self.root)
            return False
        stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        logfile=DATA_DIR/'logs'/f'upscale-{stamp}.log'
        self.batch_total=1; self.batch_index=1; self.completed=0
        self.busy=True; self.cancel.clear(); self.started=time.monotonic()
        self.phase='2倍に高精細化中'
        self.restore_button.configure(state='disabled'); self.load_button.configure(state='disabled')
        self.model_box.configure(state='disabled'); self.generate.configure(state='disabled')
        self.stop.configure(state='normal'); self.progress['value']=0
        self.progress.configure(mode='indeterminate'); self.progress.start(30)
        self.append_log(f'高精細化開始: {Path(path).name} / {model} / {size[0]}×{size[1]} → {size[0]*2}×{size[1]*2}')
        self.info.set('2倍に高精細化中。中止ボタンで停止できます。')
        threading.Thread(target=self.upscale_worker,args=(Path(path),logfile),daemon=True).start()
        return True

    def upscale_worker(self,path,logfile):
        try:
            result=upscale_image(path,OUTPUT_DIR,logfile,self.cancel,
                                 lambda line: self.events.put(('line',line)))
            if result is None:
                self.events.put(('cancelled',str(logfile)))
            else:
                self.events.put(('upscale_done',result))
                self.events.put(('batch_done',time.monotonic()-self.started))
        except Exception as error:
            self.events.put(('error',str(error)))

    def run_one(self,settings,output,logfile):
        item_started=time.monotonic()
        process=None
        watcher=None
        preview_dir=tempfile.TemporaryDirectory(prefix='studio-preview-')
        preview_path=Path(preview_dir.name)/'preview.png'
        try:
            with logfile.open('w',encoding='utf-8') as log:
                process=subprocess.Popen(command(settings,output)+['--preview','proj','--preview-interval','1','--preview-path',str(preview_path)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                def watch():
                    stamp=None
                    while process.poll() is None:
                        if self.cancel.wait(.15):
                            if process.poll() is None: process.terminate()
                            return
                        try:
                            updated=preview_path.stat().st_mtime_ns
                            if updated!=stamp:
                                with Image.open(preview_path) as image: preview=image.copy()
                                self.events.put(('preview',preview))
                                stamp=updated
                        except (OSError,ValueError): pass  # Writer may be replacing the file.
                watcher=threading.Thread(target=watch,daemon=True)
                watcher.start()
                buffer=bytearray()
                while True:
                    chunk=process.stdout.read(1)
                    if not chunk: break
                    if chunk in (b'\r',b'\n'):
                        if buffer:
                            line=ANSI.sub('',buffer.decode('utf-8',errors='replace')); buffer.clear()
                            log.write(line+'\n'); log.flush(); self.events.put(('line',line))
                    else: buffer.extend(chunk)
                if buffer: log.write(buffer.decode('utf-8',errors='replace')+'\n')
                code=process.wait()
                watcher.join()
            elapsed=time.monotonic()-item_started
            if self.cancel.is_set(): self.events.put(('cancelled',str(logfile)))
            elif code!=0 or not output.exists(): self.events.put(('error',f'生成に失敗しました（終了コード {code}）。\nログ: {logfile}'))
            else:
                metadata=dict(settings,seconds=round(elapsed,2),model=MODELS[settings['model_id']]['label'],engine_args=MODELS[settings['model_id']].get('extra_args',[]))
                output=finish_image(output,metadata)
                self.events.put(('image_done',(output,metadata)))
                return True
        except Exception as e:
            if process and process.poll() is None: process.terminate(); process.wait()
            self.events.put(('error',str(e)))
        finally:
            if watcher is not None: watcher.join()
            preview_dir.cleanup()

    def append_log(self,line):
        self.log.configure(state='normal'); self.log.insert('end',line+'\n')
        if int(self.log.index('end-1c').split('.')[0])>250: self.log.delete('1.0','80.0')
        self.log.see('end'); self.log.configure(state='disabled')

    def poll(self):
        for _ in range(300):
            try: event,data=self.events.get_nowait()
            except queue.Empty: break
            if event=='line':
                self.append_log(data)
                match=re.search(r'\|\s*(\d+)/(\d+)\s*-\s*[\d.]+s/it',data)
                if match:
                    current,total=map(int,match.groups()); self.progress['value']=100*(self.completed+current/max(total,1))/self.batch_total
                    self.phase=f'画像を生成中  {current}/{total} ステップ'
                if 'decoding' in data or 'decode_first_stage' in data: self.phase='画像を書き出し中'
            elif event=='item_start':
                self.batch_index=data
                self.phase='モデルを読み込み中'
                self.progress['value']=100*self.completed/self.batch_total
            elif event=='preview':
                self.original=data
                self.draw_preview()
                self.info.set('生成途中の簡易プレビュー（色・細部は完成画像と異なります）')
            elif event in ('image_done','upscale_done'):
                output,meta=data; self.completed+=1; self.show_image(output)
                if self.gallery is not None: self.gallery.refresh()
                self.progress['value']=100*self.completed/self.batch_total
                if event=='upscale_done':
                    up=meta['upscale']
                    self.info.set(f"2倍に高精細化  /  {up['output_width']} × {up['output_height']}  /  {up['model']}\n{output.name}")
                    self.append_log('高精細化を保存: '+str(output))
                    if self.gallery is not None and self.gallery.viewer is not None:
                        self.gallery.open_viewer(output)
                else:
                    self.info.set(f"{meta['model']}  /  {meta['width']} × {meta['height']}  /  {meta['steps']} steps  /  seed {meta['seed']}\n{output.name}")
            else:
                self.progress.stop(); self.progress.configure(mode='determinate')
                self.restore_button.configure(state='normal'); self.load_button.configure(state='normal')
                self.model_box.configure(state='readonly')
                self.busy=False; self.generate.configure(state='normal'); self.stop.configure(state='disabled')
                if event=='batch_done':
                    self.progress['value']=100
                    self.status.set(f'完了  •  {self.completed}枚 / {data:.1f}秒')
                elif event=='cancelled':
                    self.status.set(f'中止  •  {self.completed}/{self.batch_total}枚を保存済み')
                else: self.status.set(f'処理失敗  •  {self.completed}/{self.batch_total}枚を保存済み'); messagebox.showerror('処理エラー',data)
        if self.busy: self.status.set(f'{self.batch_index}/{self.batch_total}枚目  •  {self.phase}  •  {time.monotonic()-self.started:.0f}秒')
        self.root.after(100,self.poll)

    def show_image(self,path):
        try:
            with Image.open(path) as image: self.original=image.copy()
        except (OSError,ValueError,tk.TclError) as e: self.append_log('プレビューを表示できません: '+str(e)); return
        self.last_image=path; self.open_button.configure(state='normal'); self.draw_preview()

    def resize(self,event):
        if self.resize_job: self.root.after_cancel(self.resize_job)
        self.resize_job=self.root.after(100,self.draw_preview)

    def draw_preview(self):
        self.resize_job=None; self.canvas.delete('all')
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        if not self.original:
            self.canvas.create_text(w/2,h/2,text='YOUR NEXT IMAGE',fill=MUTED,font=('Yu Gothic UI',14)); return
        preview=self.original.copy()
        preview.thumbnail((max(1,w-24),max(1,h-24)),Image.Resampling.LANCZOS)
        self.photo=ImageTk.PhotoImage(preview)
        self.canvas.create_image(w/2,h/2,image=self.photo)

    def stop_run(self):
        if self.busy: self.cancel.set(); self.phase='中止処理中'; self.stop.configure(state='disabled')

    def open_folder(self): os.startfile(str(OUTPUT_DIR))
    def open_gallery(self):
        if self.gallery is None:
            self.gallery=ImageGallery(self,OUTPUT_DIR)
        else:
            self.gallery.refresh()
            self.gallery.window.deiconify()
            self.gallery.window.lift()

    def open_image(self):
        if self.last_image: os.startfile(str(self.last_image))

    def choose_settings(self):
        if self.busy: return
        path=filedialog.askopenfilename(parent=self.root,title='設定を呼び戻す画像を選択',initialdir=str(OUTPUT_DIR),filetypes=[('生成画像','*.png *.jpg *.jpeg'),('設定JSON（旧形式）','*.json')])
        if path: self.restore_settings(Path(path))

    def restore_current(self):
        if self.busy: return
        if self.last_image: self.restore_settings(self.last_image)
        else: messagebox.showinfo('設定の読み込み','先に保存画像を選択してください。',parent=self.root)

    def restore_settings(self,path):
        if self.busy: return False
        path=Path(path)
        try:
            metadata_path=path
            data=read_settings(path)
            if not isinstance(data,dict): raise ValueError('設定JSONの形式が正しくありません。')
            model_id=data.get('model_id')
            if model_id is None:
                aliases={v['label']:k for k,v in MODELS.items()}
                aliases['Z-Image Turbo Q3_K']='z-image-turbo'
                model_id=aliases.get(data.get('model'))
            if model_id not in MODELS: raise ValueError('この画像の生成モデルは未対応です。')
            prompt=data.get('prompt')
            if not isinstance(prompt,str) or not prompt.strip(): raise ValueError('プロンプトが記録されていません。')
            for key,minimum,maximum in [('width',256,2048),('height',256,2048),('steps',1,50),('seed',0,4294967295)]:
                value=data.get(key)
                if type(value) is not int or not minimum<=value<=maximum:
                    raise ValueError(f'{key}の設定値が正しくありません。')
            if data['width']%16 or data['height']%16: raise ValueError('解像度は16の倍数である必要があります。')
            options=conditioning(dict(data,model_id=model_id))
            # Decode before changing any fields, so errors leave the current form intact.
            image_path=path if path.suffix.lower()!='.json' else next((path.with_suffix(ext) for ext in ('.png','.jpg','.jpeg') if path.with_suffix(ext).is_file()),path.with_suffix('.png'))
            original=None
            if image_path.is_file():
                with Image.open(image_path) as image: original=image.copy()
            output_format=data.get('format','JPEG' if image_path.suffix.lower() in ('.jpg','.jpeg') else 'PNG')
            quality=data.get('jpeg_quality',95)
            if output_format not in ('PNG','JPEG') or type(quality) is not int or not 80<=quality<=100: raise ValueError('保存形式の設定値が正しくありません。')
        except (OSError,ValueError,TypeError,tk.TclError) as e:
            messagebox.showerror('設定を読み込めません',str(e),parent=self.root)
            return False
        self.model_label.set(MODELS[model_id]['label'])
        self.change_model(reset_steps=False)
        self.prompt.delete('1.0','end'); self.prompt.insert('1.0',prompt)
        self.preset(data['width'],data['height'])
        self.steps.set(str(data['steps'])); self.seed.set(str(data['seed']))
        self.cfg.set(str(options['cfg'])); self.negative_prompt=options['negative_prompt']; self.loras=options['loras']
        self.update_conditioning_summary()
        # Restore one selected image, not the whole original batch.
        self.count.set('1')
        self.output_format.set(output_format); self.jpeg_quality.set(str(quality))
        if original is not None:
            self.original=original; self.last_image=image_path
            self.open_button.configure(state='normal'); self.draw_preview()
        else:
            self.original=None; self.last_image=None; self.open_button.configure(state='disabled'); self.draw_preview()
        self.progress['value']=0
        self.info.set(f"{MODELS[model_id]['label']}  /  {data['width']} × {data['height']}  /  {data['steps']} steps  /  seed {data['seed']}\n{metadata_path.name}")
        self.status.set('設定を読み込みました  •  枚数は1枚。生成ボタンで再生成できます')
        self.append_log('設定を読み込み: '+str(metadata_path))
        if data.get('import_warnings'):
            messagebox.showinfo('Draw Thingsからの読み込み',data['import_warnings'],parent=self.root)
        return True

    def close(self):
        if self.busy:
            if not messagebox.askyesno('処理を中止', '実行中の処理を中止して閉じますか？'): return
            self.stop_run(); self.wait_close(); return
        self.root.destroy()

    def wait_close(self):
        if self.busy: self.root.after(150,self.wait_close)
        else: self.root.destroy()

if __name__=='__main__':
    root=tk.Tk(); app=App(root); root.mainloop()
