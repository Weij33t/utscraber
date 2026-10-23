"""Double-click bulk-export.cmd to open this local desktop exporter."""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend.bulk import export


def main():
    root = tk.Tk()
    root.title('Текстом · Выгрузить субтитры канала')
    root.geometry('780x560')
    root.minsize(620, 450)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Все субтитры в одну папку', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Работает напрямую с YouTube на этом ПК. Supadata и кредиты не используются.').pack(anchor='w', pady=(8, 18))
    ttk.Label(frame, text='Ссылка на канал, @имя или ссылки на видео (по одной на строке):').pack(anchor='w')
    source = tk.Text(frame, height=7, font=('Segoe UI', 11), wrap='word')
    source.pack(fill='both', expand=True, pady=(6, 12))
    folder = tk.StringVar(value=str(Path.home() / 'Downloads' / 'Tekstom'))
    row = ttk.Frame(frame)
    row.pack(fill='x')
    ttk.Entry(row, textvariable=folder).pack(side='left', fill='x', expand=True)
    ttk.Button(row, text='Папка…', command=lambda: choose_folder()).pack(side='left', padx=(8, 0))
    status = tk.StringVar(value='TXT для каждого видео, общий all-transcripts.txt и отчёт о пропусках.')
    ttk.Label(frame, textvariable=status, wraplength=700).pack(anchor='w', pady=16)
    controls = ttk.Frame(frame)
    controls.pack(fill='x')
    events = queue.Queue()
    cancel = threading.Event()
    active = False

    def choose_folder():
        value = filedialog.askdirectory(initialdir=folder.get())
        if value:
            folder.set(value)

    def run():
        nonlocal active
        value = source.get('1.0', 'end').strip()
        destination = folder.get().strip()
        if not value or not destination:
            messagebox.showinfo('Текстом', 'Укажи канал или видео и папку для текстов.')
            return
        active = True
        cancel.clear()
        start.configure(state='disabled')
        stop.configure(state='normal')
        source.configure(state='disabled')
        status.set('Получаю список видео. Для большого канала выгрузка может занять долгое время…')
        def worker():
            try:
                result = export(value, destination, events.put, cancel)
                events.put({'done': result})
            except Exception as error:
                events.put({'error': getattr(error, 'detail', str(error))})
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        nonlocal active
        try:
            while True:
                event = events.get_nowait()
                if 'message' in event:
                    status.set(event['message'])
                else:
                    active = False
                    start.configure(state='normal')
                    stop.configure(state='disabled')
                    source.configure(state='normal')
                    if 'error' in event:
                        status.set('Ошибка: ' + str(event['error']))
                    else:
                        report = event['done']
                        end = 'IP заблокирован; повтори позже.' if report['blocked'] else 'Остановлено.' if report['cancelled'] else 'Готово.'
                        status.set(f'{end} Сохранено: {report["saved"]}, из кеша: {report["cached"]}, пропущено: {len(report["skipped"])}. Подробности в report.json.')
        except queue.Empty:
            pass
        root.after(200, poll)

    def close():
        if active:
            cancel.set()
            status.set('Останавливаю после текущего запроса. Затем можно закрыть окно.')
        else:
            root.destroy()

    start = ttk.Button(controls, text='Скачать тексты', command=run)
    start.pack(side='left')
    stop = ttk.Button(controls, text='Остановить', state='disabled', command=cancel.set)
    stop.pack(side='left', padx=8)
    ttk.Button(controls, text='Открыть папку', command=lambda: os.startfile(folder.get()) if Path(folder.get()).is_dir() else None).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(200, poll)
    root.mainloop()


if __name__ == '__main__':
    main()
