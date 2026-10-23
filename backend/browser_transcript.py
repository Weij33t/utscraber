"""Read the transcript panel through a visible browser, without transcript APIs."""
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROWS = 'transcript-segment-view-model, ytd-transcript-segment-renderer'
SHOW = re.compile(r'Показать (?:текст видео|транскрипт|расшифровку)|Show transcript', re.I)
READ_ROWS = r'''elements => elements.map(element => ({
    timestamp: (element.querySelector('.ytwTranscriptSegmentViewModelTimestamp, .segment-timestamp')?.textContent || '').trim(),
    text: (element.querySelector('span[role="text"], .segment-text')?.textContent || '').trim()
})).filter(row => row.timestamp && row.text)'''


class BrowserSetupError(RuntimeError):
    pass


class BrowserBlockedError(RuntimeError):
    pass


class BrowserStopped(RuntimeError):
    pass


def parse_rows(rows, duration=None):
    result = []
    seen = set()
    for row in rows:
        timestamp, text = row.get('timestamp', '').strip(), row.get('text', '').strip()
        if not re.fullmatch(r'\d+(?::[0-5]\d){1,2}', timestamp) or not text:
            continue
        seconds = 0
        for part in timestamp.split(':'):
            seconds = seconds * 60 + int(part)
        key = (seconds, text)
        if key not in seen:
            seen.add(key)
            result.append({'start': seconds, 'duration': 0, 'text': text})
    result.sort(key=lambda row: row['start'])
    for index, row in enumerate(result):
        end = result[index + 1]['start'] if index + 1 < len(result) else duration
        row['duration'] = max(0, end - row['start']) if isinstance(end, (float, int)) else 0
    return result


class BrowserTranscriptReader:
    def __init__(self, cancel=None, notify=lambda message: None, profile=None, headless=False):
        self.cancel = cancel or threading.Event()
        self.notify = notify
        self.profile = Path(profile) if profile else ROOT / '.tools' / 'youtube-browser'
        self.headless = headless
        self.driver = self.context = self.page = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserSetupError('Для режима браузера нужны зависимости из requirements-desktop.txt.') from error
        self.driver = sync_playwright().start()
        try:
            self.profile.mkdir(parents=True, exist_ok=True)
            for channel in ('msedge', 'chrome', 'chromium'):
                try:
                    self.context = self.driver.chromium.launch_persistent_context(
                        str(self.profile), channel=channel, headless=self.headless,
                        locale='ru-RU', viewport={'width': 1280, 'height': 850},
                        args=['--mute-audio'])
                    break
                except Exception as error:
                    message = str(error)
                    if 'Executable doesn\'t exist' not in message and 'distribution' not in message:
                        raise BrowserSetupError('Не удалось открыть браузер. Закройте предыдущее окно браузерной выгрузки и попробуйте снова.') from error
            if self.context is None:
                raise BrowserSetupError('Не найден браузер: установите Microsoft Edge или Google Chrome.')
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(5000)
            self.page.route('**/*', lambda route: route.abort() if route.request.resource_type == 'media' else route.continue_())
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.driver:
                self.driver.stop()

    def check_cancel(self):
        if self.cancel.is_set():
            raise BrowserStopped('Выгрузка остановлена.')
        if self.page.is_closed():
            raise BrowserStopped('Окно браузера закрыто. Выгрузка остановлена.')

    def fetch(self, ident):
        self.check_cancel()
        self.notify('Открываю видео в браузере…')
        self.page.goto(f'https://www.youtube.com/watch?v={ident}&hl=ru&autoplay=0',
                       wait_until='domcontentloaded', timeout=45000)
        reject = self.page.get_by_role('button', name=re.compile(r'^(Отклонить все|Reject all)$', re.I))
        if reject.count() and reject.first.is_visible():
            reject.first.click()
        self.page.locator('ytd-watch-metadata h1').first.wait_for(state='visible', timeout=25000)
        self.page.locator('video').evaluate_all('videos => videos.forEach(video => video.pause())')
        title = self.page.locator('ytd-watch-metadata h1').first.inner_text().strip()
        self.check_cancel()
        show = self.page.get_by_role('button', name=SHOW).filter(visible=True)
        if not show.count():
            self.notify('Раскрываю описание…')
            expand = self.page.locator('#description-inline-expander #expand').first
            if expand.count() and expand.is_visible():
                expand.click()
            else:
                more = self.page.locator('ytd-watch-metadata #description').get_by_role(
                    'button', name=re.compile(r'^(?:\.{3}|…)?\s*(?:ещё|more)$', re.I)).first
                if more.count() and more.is_visible():
                    more.click()
        try:
            show.first.wait_for(state='visible', timeout=10000)
        except Exception as error:
            body = self.page.locator('body').inner_text()
            if re.search(r'confirm you.re not a bot|подтвердите,? что вы не бот|unusual traffic|необычн.*трафик', body, re.I):
                raise BrowserBlockedError('YouTube запросил проверку в браузере. Выгрузка остановлена; смените подключение или пройдите проверку вручную.') from error
            raise LookupError('В описании нет кнопки «Показать текст видео». Возможно, субтитры недоступны.') from error
        self.notify('Открываю текст видео…')
        show.first.click()
        rows = self.page.locator(ROWS)
        try:
            rows.first.wait_for(state='visible', timeout=20000)
        except Exception as error:
            raise LookupError('YouTube не загрузил строки транскрипта в браузере.') from error
        self.check_cancel()
        # Collect every DOM row, including rows outside the visible scroll area.
        raw = rows.evaluate_all(READ_ROWS)
        previous = -1
        for _ in range(8):
            self.check_cancel()
            if len(raw) == previous:
                break
            previous = len(raw)
            rows.last.scroll_into_view_if_needed()
            self.page.wait_for_timeout(600)
            raw = rows.evaluate_all(READ_ROWS)
        duration = self.page.locator('video').first.evaluate('video => Number.isFinite(video.duration) ? video.duration : null') if self.page.locator('video').count() else None
        segments = parse_rows(raw, duration)
        if not segments:
            raise LookupError('Панель транскрипта открылась, но не удалось прочитать строки с тайм-кодами.')
        return {'id': ident, 'title': title or ident, 'language': 'und',
                'segments': segments, 'source': 'browser'}
