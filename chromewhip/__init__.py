import asyncio.subprocess
import logging
import logging.config
import platform
import signal
import os

from aiohttp import web
import yaml

from chromewhip.chrome import Chrome
from chromewhip.middleware import error_middleware
from chromewhip.routes import setup_routes


log = logging.getLogger(__name__)

HOST = '127.0.0.1'
PORT = 9222
NUM_TABS = 4
DISPLAY = ':99'

async def on_shutdown(app):
    c = app['chrome-driver']
    if c.is_connected:
        for tab in c.tabs:
            await tab.disconnect()

    chrome = app['chrome-process']
    chrome.send_signal(signal.SIGINT)
    try:
        returncode = await asyncio.wait_for(chrome.wait(), timeout=15)
        if not returncode:
            log.error('Timed out trying to shutdown Chrome gracefully!')
        elif returncode < 0:
            log.error('Error code "%s" received while shutting down Chrome!' % abs(returncode))
        else:
            log.debug("Successfully shut down Chrome!")
    except asyncio.TimeoutError:
        log.error('Timed out trying to shutdown Chrome gracefully!')


def setup_chrome(env: dict = None, loop: asyncio.AbstractEventLoop = None):
    # TODO: manage process lifecycle in coro
    system = platform.system()
    if system == 'Darwin':
        chrome_fp = '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary'
    elif system == 'Linux':
        # chrome_fp = '/usr/bin/google-chrome-stable'
        chrome_fp = '/opt/google/chrome/chrome'
    tab_url = 'about:blank'
    flags = [
        # '--disable-gpu',
        # '--headless',
        '--window-size=1920,1080',
        '--enable-logging',
        '--hide-scrollbars',
        # '--no-sandbox',
        '--no-first-run',
        '--remote-debugging-address=%s' % HOST,
        '--remote-debugging-port=%s' % PORT,
        '--user-data-dir=/tmp',
    ]
    flags.extend([tab_url] * NUM_TABS)
    args = [chrome_fp] + flags
    chrome = asyncio.subprocess.create_subprocess_exec(*args, env=env, loop=loop)
    return chrome


def setup_xvfb(env: dict = None, loop: asyncio.AbstractEventLoop = None):
    # TODO: manage process lifecycle in coro
    system = platform.system()
    if system != 'Linux':
        raise OSError('Only linux supported for running xvfb')
    flags = [
        DISPLAY,
        '-ac',
        '-screen',
        '0',
        '1920x1080x16',
        '+extension',
        'RANDR',
        '-nolisten',
        'tcp',
    ]
    args = ['/usr/bin/Xvfb'] + flags
    xvfb = asyncio.subprocess.create_subprocess_exec(*args, env=env, loop=loop)
    return xvfb


def setup_app(loop=None, js_profiles_path=None):
    app = web.Application(loop=loop, middlewares=[error_middleware])

    js_profiles = {}

    if js_profiles_path:
        root, _, files, _ = next(os.fwalk(js_profiles_path))
        js_files = filter(lambda f: os.path.splitext(f)[1] == '.js', files)
        _, profile_name = os.path.split(root)
        log.debug('adding profile "{}"'.format(profile_name))
        js_profiles[profile_name] = ""
        for f in js_files:
            code = open(os.path.join(root, f)).read()
            js_profiles[profile_name] += '{}\n'.format(code)

    app.on_shutdown.append(on_shutdown)

    c = Chrome(host=HOST, port=PORT)

    app['chrome-driver'] = c
    app['js-profiles'] = js_profiles

    setup_routes(app)

    return app


if __name__ == '__main__':
    import argparse
    import sys
    config_fp = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config/dev.yaml'))
    config_f = open(config_fp)
    config = yaml.load(config_f)
    logging.config.dictConfig(config['logging'])
    # logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser()
    parser.add_argument('--js-profiles-path',
                        help="path to a folder with javascript profiles")
    args = parser.parse_args(sys.argv[1:])
    kwargs = {}
    if args.js_profiles_path:
        kwargs['js_profiles_path'] = args.js_profiles_path

    loop = asyncio.get_event_loop()

    env = {
       'DISPLAY': DISPLAY
    }

    app = setup_app(**kwargs, loop=loop)

    system = platform.system()
    if system == 'Linux':
        xvfb = setup_xvfb(env=env, loop=loop)
        xvfb_future = loop.run_until_complete(xvfb)
        log.debug('Started xvfb!')
        app['xvfb-process'] = xvfb_future

    chrome = setup_chrome(env=env, loop=loop)
    chrome_future = loop.run_until_complete(chrome)
    log.debug('Started Chrome!')
    app['chrome-process'] = chrome_future

    # TODO: need indication from chrome process to start http server
    loop.run_until_complete(asyncio.sleep(3))
    loop.run_until_complete(app['chrome-driver'].connect())
    web.run_app(app)