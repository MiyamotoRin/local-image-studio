"""Local desktop UI for the verified stable-diffusion.cpp installation."""
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk
from image_storage import read_settings, finish_image
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
    '1:1 正方形': ((512,512), (1024,1024)),
    '3:2 横長': ((768,512), (1248,832)),
    '2:3 縦長': ((512,768), (832,1248)),
    '4:3 横長': ((640,480), (1152,864)),
    '3:4 縦長': ((480,640), (864,1152)),
    '16:9 ワイド': ((768,432), (1280,720)),
    '9:16 縦ワイド': ((432,768), (720,1280)),
    '4:5 縦長': ((512,640), (896,1120)),
    '5:4 横長': ((640,512), (1120,896)),
}
SIZE_LEVELS = ('軽量', '高解像度（約100万画素）')


MODELS = {
    'anima-turbo': dict(label='Anima Turbo 1.1 · Q4_K_M', steps=8, diffusion='anima-turbo-v1.1-Q4_K_M.gguf', llm='qwen_3_06b_base.safetensors', vae='qwen_image_vae.safetensors', extra_args=['--sampling-method','euler']),
    'z-image-turbo': dict(label='Z-Image Turbo · Q3_K', steps=8, diffusion='z_image_turbo-Q3_K.gguf', llm='Qwen3-4B-Instruct-2507-Q4_K_M.gguf', vae='ae.safetensors'),
    'flux2-klein-4b': dict(label='FLUX.2 klein 4B · Q4_0', steps=4, diffusion='flux-2-klein-4b-Q4_0.gguf', llm='Qwen3-4B-Q4_K_M.gguf', vae='flux2-vae.safetensors'),
}

def command(settings, output):
    model = MODELS[settings.get("model_id", "z-image-turbo")]
    return [str(ENGINE),
            '--diffusion-model', str(MODELS_DIR/model['diffusion']),
            '--llm', str(MODELS_DIR/model['llm']),
            '--vae', str(MODELS_DIR/model['vae']),
            '--offload-to-cpu', '--max-vram', str(CONFIG['max_vram']), '--cfg-scale', '1.0',
            '--steps', str(settings['steps']), '-W', str(settings['width']),
            '-H', str(settings['height']), '--seed', str(settings['seed']),
            '-p', settings['prompt'], '-o', str(output)] + model.get('extra_args', [])

class App:
    def __init__(self, root):
        for folder in (OUTPUT_DIR,DATA_DIR,DATA_DIR/'logs'): folder.mkdir(parents=True,exist_ok=True)
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
        root.title('Local Image Studio')
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
        head = ttk.Frame(root, padding=(28, 22, 28, 16)); head.pack(fill='x')
        ttk.Label(head, text='Local Image Studio', style='Title.TLabel').pack(side='left')
        self.model_label=tk.StringVar(value=MODELS['z-image-turbo']['label'])
        self.model_box=ttk.Combobox(head,textvariable=self.model_label,values=[m['label'] for m in MODELS.values()],state='readonly',width=29)
        self.model_box.pack(side='right')
        ttk.Label(head,text='生成モデル  ',style='Muted.TLabel').pack(side='right')
        self.model_box.bind('<<ComboboxSelected>>',self.change_model)
        body = ttk.Frame(root, padding=(28, 0, 28, 16)); body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1); body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, width=380); left.grid(row=0, column=0, sticky='nsew', padx=(0, 24))
        left.grid_propagate(False); left.columnconfigure(0, weight=1); left.rowconfigure(2, weight=1)
        ttk.Label(left, text='作りたい画像', font=('Yu Gothic UI', 13, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(left, text='被写体・光・場所を具体的に。英語がおすすめです。', style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(4, 10))
        self.prompt = tk.Text(left, height=9, wrap='word', bg=PANEL, fg=TEXT, insertbackground=TEXT,
                              relief='flat', padx=12, pady=12, font=('Yu Gothic UI', 11), undo=True)
        self.prompt.grid(row=2, column=0, sticky='nsew')
        self.prompt.insert('1.0', (DATA_DIR/'prompt.txt').read_text(encoding='utf-8-sig') if (DATA_DIR/'prompt.txt').exists() else '')
        params = ttk.Frame(left); params.grid(row=3, column=0, sticky='ew', pady=(18, 0))
        self.width, self.height, self.steps, self.seed = [tk.StringVar(value=v) for v in ('512', '512', '8', '-1')]
        self.count = tk.StringVar(value='1')
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
        ttk.Label(params,text='seed  /  -1: 毎枚ランダム・指定値: 1ずつ加算').pack(anchor='w',pady=(12,4))
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
        ttk.Label(params,textvariable=self.model_tip,style='Muted.TLabel',wraplength=375).pack(anchor='w',pady=(12,14))
        actions=ttk.Frame(left); actions.grid(row=4,column=0,sticky='ew')
        self.generate=ttk.Button(actions,text='画像を生成',style='Accent.TButton',command=self.start)
        self.generate.pack(side='left',fill='x',expand=True)
        self.stop=ttk.Button(actions,text='中止',command=self.stop_run,state='disabled'); self.stop.pack(side='left',padx=(8,0))
        self.status=tk.StringVar(value='準備完了  •  設定を変更して生成できます')
        ttk.Label(left,textvariable=self.status,style='Muted.TLabel',wraplength=375).grid(row=5,column=0,sticky='w',pady=(12,6))
        self.progress=ttk.Progressbar(left,mode='determinate',maximum=100); self.progress.grid(row=6,column=0,sticky='ew')
        right=ttk.Frame(body); right.grid(row=0,column=1,sticky='nsew'); right.columnconfigure(0,weight=1); right.rowconfigure(1,weight=1)
        bar=ttk.Frame(right); bar.grid(row=0,column=0,sticky='ew',pady=(0,10))
        ttk.Label(bar,text='プレビュー',font=('Yu Gothic UI',13,'bold')).pack(side='left')
        ttk.Button(bar,text='保存先を開く',command=self.open_folder).pack(side='right')
        self.canvas=tk.Canvas(right,bg='#0c1218',highlightthickness=1,highlightbackground='#293b48')
        self.canvas.grid(row=1,column=0,sticky='nsew'); self.canvas.bind('<Configure>',self.resize)
        self.info=tk.StringVar(value='完成した画像がここに表示されます')
        ttk.Label(right,textvariable=self.info,style='Muted.TLabel',wraplength=620).grid(row=2,column=0,sticky='w',pady=(10,0))
        image_actions=ttk.Frame(right); image_actions.grid(row=3,column=0,sticky='ew',pady=(8,0))
        self.open_button=ttk.Button(image_actions,text='画像を開く',command=self.open_image,state='disabled'); self.open_button.pack(side='left')
        self.restore_button=ttk.Button(image_actions,text='この画像の設定を戻す',command=self.restore_current); self.restore_button.pack(side='left',padx=(8,0))
        self.load_button=ttk.Button(image_actions,text='保存画像から読み込む',command=self.choose_settings); self.load_button.pack(side='left',padx=(8,0))
        footer=ttk.Frame(root,padding=(28,0,28,20)); footer.pack(fill='x')
        ttk.Label(footer,text='実行ログ',style='Muted.TLabel').pack(anchor='w',pady=(0,5))
        self.log=tk.Text(footer,height=5,bg=PANEL,fg=MUTED,relief='flat',font=('Consolas',9),padx=10,pady=8,state='disabled',wrap='word')
        self.log.pack(fill='x')
        try:
            saved=json.loads((DATA_DIR/'ui-settings.json').read_text(encoding='utf-8'))
            self.count.set(str(saved.get('count',1)))
            self.output_format.set(saved.get('format','PNG'))
            self.jpeg_quality.set(str(saved.get('jpeg_quality',95)))
            self.model_label.set(MODELS.get(saved.get('model_id'),MODELS['z-image-turbo'])['label'])
            for name,var in [('width',self.width),('height',self.height),('steps',self.steps),('seed',self.seed)]: var.set(str(saved[name]))
        except (OSError,ValueError,KeyError): pass
        self.change_model(reset_steps=False)
        images=sorted(p for p in (OUTPUT_DIR).iterdir() if p.suffix.lower() in ('.png','.jpg','.jpeg'))
        if images: self.show_image(images[-1]); self.info.set('前回の画像  •  '+images[-1].name)
        root.after(100,self.poll)

    def update_format_controls(self,*args):
        if self.output_format.get()=='JPEG':
            self.jpeg_controls.pack(side='left')
        else:
            self.jpeg_controls.pack_forget()

    def model_id(self):
        return next(k for k,v in MODELS.items() if v['label']==self.model_label.get())

    def change_model(self,event=None,reset_steps=True):
        model=MODELS[self.model_id()]
        if reset_steps: self.steps.set(str(model['steps']))
        self.model_tip.set(f"推奨 {model['steps']}ステップ・CFG 1.0。幅と高さは16の倍数。\n" + ("アニメ向け。英語の文章やタグで指定できます。" if self.model_id()=="anima-turbo" else "大きな画像ほど時間・メモリが必要です。"))

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
        if not -1<=seed<=2147483647: raise ValueError('seedは-1〜2147483647にしてください。')
        if not 1<=count<=100: raise ValueError('枚数は1〜100にしてください。')
        if self.output_format.get() not in ('PNG','JPEG'): raise ValueError('保存形式はPNGまたはJPEGを選んでください。')
        quality=int(self.jpeg_quality.get())
        if not 80<=quality<=100: raise ValueError('JPEG品質は80〜100にしてください。')
        return dict(prompt=prompt,width=w,height=h,steps=steps,seed=seed,model_id=self.model_id(),count=count,format=self.output_format.get(),jpeg_quality=quality)

    def start(self):
        if self.busy: return
        try:
            settings=self.settings()
            model=MODELS[settings['model_id']]
            for file in [ENGINE]+[MODELS_DIR/model[k] for k in ('diffusion','llm','vae')]:
                if not file.is_file(): raise ValueError('必要なファイルがありません: '+str(file)+'\nREADMEのセットアップ手順を確認してください。')
            (DATA_DIR/'prompt.txt').write_text(settings['prompt']+'\n',encoding='utf-8')
            (DATA_DIR/'ui-settings.json').write_text(json.dumps({k:v for k,v in settings.items() if k!='prompt'},indent=2),encoding='utf-8')
        except (ValueError,OSError) as e: messagebox.showerror('設定を確認してください',str(e)); return
        stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output=OUTPUT_DIR/f'image-{stamp}.png'
        logfile=DATA_DIR/'logs'/f'ui-{stamp}.log'
        self.batch_total=settings['count']; self.batch_index=1; self.completed=0
        self.restore_button.configure(state='disabled'); self.load_button.configure(state='disabled')
        self.model_box.configure(state='disabled')
        self.busy=True; self.cancel.clear(); self.started=time.monotonic(); self.phase='モデルを読み込み中'
        self.generate.configure(state='disabled'); self.stop.configure(state='normal'); self.progress['value']=0
        self.append_log(f"生成開始: {settings['count']}枚 / {settings['width']}×{settings['height']} / {settings['steps']} steps / seed {settings['seed']}")
        self.info.set('生成中。前回の画像は完了まで表示されます。')
        threading.Thread(target=self.worker,args=(settings,output,logfile),daemon=True).start()

    def worker(self,settings,output,logfile):
        try:
            # One subprocess at a time keeps GPU use independent of image count.
            random_seeds = secrets.SystemRandom().sample(range(2147483648),settings['count']) if settings['seed']==-1 else None
            for index in range(settings['count']):
                if self.cancel.is_set():
                    self.events.put(('cancelled',str(logfile))); return
                seed=random_seeds[index] if random_seeds is not None else (settings['seed']+index)%2147483648
                item=dict(settings,seed=seed,batch_index=index+1)
                suffix=f'-{index+1:03d}' if settings['count']>1 else ''
                destination=output.with_name(output.stem+suffix+output.suffix)
                item_log=logfile.with_name(logfile.stem+suffix+logfile.suffix)
                self.events.put(('item_start',index+1))
                if not self.run_one(item,destination,item_log): return
            self.events.put(('batch_done',time.monotonic()-self.started))
        except Exception as e:
            self.events.put(('error',str(e)))

    def run_one(self,settings,output,logfile):
        item_started=time.monotonic()
        process=None
        try:
            with logfile.open('w',encoding='utf-8') as log:
                process=subprocess.Popen(command(settings,output),cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                def watch():
                    while process.poll() is None:
                        if self.cancel.wait(.15):
                            if process.poll() is None: process.terminate()
                            return
                threading.Thread(target=watch,daemon=True).start()
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
            elapsed=time.monotonic()-item_started
            if self.cancel.is_set(): self.events.put(('cancelled',str(logfile)))
            elif code!=0 or not output.exists(): self.events.put(('error',f'生成に失敗しました（終了コード {code}）。\nログ: {logfile}'))
            else:
                metadata=dict(settings,seconds=round(elapsed,2),model=MODELS[settings['model_id']]['label'],cfg=1.0)
                output=finish_image(output,metadata)
                self.events.put(('image_done',(output,metadata)))
                return True
        except Exception as e:
            if process and process.poll() is None: process.terminate(); process.wait()
            self.events.put(('error',str(e)))

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
            elif event=='image_done':
                output,meta=data; self.completed+=1; self.show_image(output)
                self.progress['value']=100*self.completed/self.batch_total
                self.info.set(f"{meta['model']}  /  {meta['width']} × {meta['height']}  /  {meta['steps']} steps  /  seed {meta['seed']}\n{output.name}")
            else:
                self.restore_button.configure(state='normal'); self.load_button.configure(state='normal')
                self.model_box.configure(state='readonly')
                self.busy=False; self.generate.configure(state='normal'); self.stop.configure(state='disabled')
                if event=='batch_done':
                    self.progress['value']=100
                    self.status.set(f'完了  •  {self.completed}枚 / {data:.1f}秒')
                elif event=='cancelled':
                    self.status.set(f'中止  •  {self.completed}/{self.batch_total}枚を保存済み')
                else: self.status.set(f'生成失敗  •  {self.completed}/{self.batch_total}枚を保存済み'); messagebox.showerror('生成エラー',data)
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
            for key,minimum,maximum in [('width',256,2048),('height',256,2048),('steps',1,50),('seed',0,2147483647)]:
                value=data.get(key)
                if type(value) is not int or not minimum<=value<=maximum:
                    raise ValueError(f'{key}の設定値が正しくありません。')
            if data['width']%16 or data['height']%16: raise ValueError('解像度は16の倍数である必要があります。')
            if data.get('cfg',1.0)!=1.0: raise ValueError('この画像のCFGは現在のUI（1.0固定）では再現できません。')
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
        return True

    def close(self):
        if self.busy:
            if not messagebox.askyesno('生成を中止', '生成を中止して閉じますか？'): return
            self.stop_run(); self.wait_close(); return
        self.root.destroy()

    def wait_close(self):
        if self.busy: self.root.after(150,self.wait_close)
        else: self.root.destroy()

if __name__=='__main__':
    root=tk.Tk(); app=App(root); root.mainloop()
