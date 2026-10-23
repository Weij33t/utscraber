"""Double-click bulk-export.cmd to open this local desktop exporter."""
import os
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from backend.bulk import TOPICS, export


def attach_clipboard(widget):
    """Support paste buttons, context menus and Ctrl shortcuts in RU layouts."""
    def paste():
        if str(widget.cget('state')) == 'disabled':
            return 'break'
        try:
            value = widget.clipboard_get().replace('\r\n', '\n')
        except tk.TclError:
            widget.bell()
            return 'break'
        widget.focus_set()
        try:
            widget.delete('sel.first', 'sel.last')
        except tk.TclError:
            pass
        widget.insert('insert', value)
        if isinstance(widget, tk.Text):
            widget.see('insert')
        return 'break'

    def virtual(name):
        widget.focus_set()
        widget.event_generate(name)
        return 'break'

    def shortcut(event):
        # Windows virtual key codes are independent of the active language.
        if event.keycode == 86:
            return paste()
        action = {65: '<<SelectAll>>', 67: '<<Copy>>', 88: '<<Cut>>'}.get(event.keycode)
        if action:
            return virtual(action)

    menu = tk.Menu(widget, tearoff=False)
    for label, action in [('Вырезать', '<<Cut>>'), ('Копировать', '<<Copy>>')]:
        menu.add_command(label=label, command=lambda name=action: virtual(name))
    menu.add_command(label='Вставить', command=paste)
    menu.add_separator()
    menu.add_command(label='Выделить всё', command=lambda: virtual('<<SelectAll>>'))
    def popup(event):
        widget.focus_set()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return 'break'
    widget.bind('<Button-3>', popup)
    widget.bind('<Control-KeyPress>', shortcut)
    widget.bind('<Shift-Insert>', lambda event: paste())
    widget._clipboard_menu = menu
    return paste


def main(resume=False):
    root = tk.Tk()
    root.title('Текстом · Выгрузка по темам')
    root.geometry('780x620')
    root.minsize(620, 520)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Все субтитры в одну папку', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Работает напрямую с YouTube на этом ПК. Supadata и кредиты не используются.').pack(anchor='w', pady=(8, 18))
    source_row = ttk.Frame(frame)
    source_row.pack(fill='x')
    ttk.Label(source_row, text='Канал, @имя или ссылки на видео (по одной на строке):').pack(side='left')
    source = tk.Text(frame, height=7, font=('Segoe UI', 11), wrap='word')
    source.pack(fill='both', expand=True, pady=(6, 12))
    paste_source = attach_clipboard(source)
    ttk.Button(source_row, text='Вставить из буфера', command=paste_source).pack(side='right')
    folder = tk.StringVar(value=str(Path.home() / 'Downloads' / 'Tekstom'))
    row = ttk.Frame(frame)
    row.pack(fill='x')
    folder_entry = ttk.Entry(row, textvariable=folder)
    folder_entry.pack(side='left', fill='x', expand=True)
    attach_clipboard(folder_entry)
    ttk.Button(row, text='Папка…', command=lambda: choose_folder()).pack(side='left', padx=(8, 0))
    topic_row = ttk.Frame(frame)
    topic_row.pack(fill='x', pady=(12, 0))
    ttk.Label(topic_row, text='Тема:').pack(side='left', padx=(0, 8))
    topic = tk.StringVar(value='Автоматически')
    topic_box = ttk.Combobox(topic_row, textvariable=topic, values=['Автоматически', *TOPICS, 'Другие темы'])
    topic_box.pack(side='left', fill='x', expand=True)
    attach_clipboard(topic_box)
    ttk.Label(frame, text='Автоматически — по ключевым словам названия и текста. Можно вписать свою тему.').pack(anchor='w', pady=(6, 0))
    status = tk.StringVar(value='Файлы с названием и темой в texts/, общий TXT и отчёт о пропусках.')
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
        selected_topic = topic.get().strip()
        if not value or not destination:
            messagebox.showinfo('Текстом', 'Укажи канал или видео и папку для текстов.')
            return
        active = True
        cancel.clear()
        start.configure(state='disabled')
        resume_button.configure(state='disabled')
        stop.configure(state='normal')
        source.configure(state='disabled')
        topic_box.configure(state='disabled')
        status.set('Получаю список видео. Для большого канала выгрузка может занять долгое время…')
        def worker():
            try:
                result = export(value, destination, events.put, cancel,
                                topic=None if selected_topic == 'Автоматически' else selected_topic)
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
                    resume_button.configure(state='normal')
                    stop.configure(state='disabled')
                    source.configure(state='normal')
                    topic_box.configure(state='normal')
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

    def resume_export():
        try:
            report = json.loads((Path(folder.get()) / 'report.json').read_text(encoding='utf-8'))
            previous_source = report.get('source')
            if not previous_source:
                raise ValueError('В старом отчёте нет ссылки на канал. Вставь её выше и нажми «Скачать тексты»: сохранённые видео будут взяты из кеша.')
            source.delete('1.0', 'end')
            source.insert('1.0', previous_source)
            topic.set(report.get('topic') or 'Автоматически')
            run()
        except (OSError, ValueError) as error:
            messagebox.showinfo('Продолжение выгрузки', str(error))

    start = ttk.Button(controls, text='Скачать тексты', command=run)
    start.pack(side='left')
    resume_button = ttk.Button(controls, text='Продолжить из отчёта', command=resume_export)
    resume_button.pack(side='left', padx=(8, 0))
    stop = ttk.Button(controls, text='Остановить', state='disabled', command=cancel.set)
    stop.pack(side='left', padx=8)
    ttk.Button(controls, text='Открыть папку', command=lambda: os.startfile(folder.get()) if Path(folder.get()).is_dir() else None).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(200, poll)
    if resume:
        root.after(300, resume_export)
    root.mainloop()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    main(resume=parser.parse_args().resume)
