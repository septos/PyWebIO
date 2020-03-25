import asyncio
import json
import logging
import threading
import webbrowser

import tornado
import tornado.httpserver
import tornado.ioloop
import tornado.websocket
from tornado.web import StaticFileHandler
from ..session import CoroutineBasedSession, ThreadBasedSession, get_session_implement, DesignatedThreadSession, \
    mark_server_started
from ..utils import get_free_port, wait_host_port, STATIC_PATH

logger = logging.getLogger(__name__)


def webio_handler(task_func):
    class WSHandler(tornado.websocket.WebSocketHandler):

        def check_origin(self, origin):
            return True

        def get_compression_options(self):
            # Non-None enables compression with default options.
            return {}

        def send_msg_to_client(self, session: CoroutineBasedSession):
            for msg in session.get_task_commands():
                self.write_message(json.dumps(msg))

        def open(self):
            logger.debug("WebSocket opened")
            self.set_nodelay(True)

            self._close_from_session_tag = False  # 是否从session中关闭连接

            if get_session_implement() is CoroutineBasedSession:
                self.session = CoroutineBasedSession(task_func, on_task_command=self.send_msg_to_client,
                                                     on_session_close=self.close)
            else:
                self.session = ThreadBasedSession(task_func, on_task_command=self.send_msg_to_client,
                                                  on_session_close=self.close_from_session,
                                                  loop=asyncio.get_event_loop())

        def on_message(self, message):
            data = json.loads(message)
            self.session.send_client_event(data)

        def close_from_session(self):
            self._close_from_session_tag = True
            self.close()

        def on_close(self):
            if not self._close_from_session_tag:
                self.session.close(no_session_close_callback=True)
            logger.debug("WebSocket closed")

    return WSHandler


async def open_webbrowser_on_server_started(host, port):
    url = 'http://%s:%s' % (host, port)
    is_open = await wait_host_port(host, port, duration=5, delay=0.5)
    if is_open:
        logger.info('Openning %s' % url)
        webbrowser.open(url)
    else:
        logger.error('Open %s failed.' % url)


def _setup_server(webio_handler, port=0, host='', **tornado_app_settings):
    if port == 0:
        port = get_free_port()

    print('Listen on %s:%s' % (host or '0.0.0.0', port))

    handlers = [(r"/io", webio_handler),
                (r"/(.*)", StaticFileHandler, {"path": STATIC_PATH, 'default_filename': 'index.html'})]

    app = tornado.web.Application(handlers=handlers, **tornado_app_settings)
    server = app.listen(port, address=host)
    return server, port


def start_server(target, port=0, host='', debug=False,
                 auto_open_webbrowser=False,
                 session_type=None,
                 websocket_max_message_size=None,
                 websocket_ping_interval=None,
                 websocket_ping_timeout=None,
                 **tornado_app_settings):
    """Start a Tornado server to serve `target` function

    :param target: task function. It's a coroutine function is use CoroutineBasedSession or
        a simple function is use ThreadBasedSession.
    :param port: server bind port. set ``0`` to find a free port number to use
    :param host: server bind host. ``host`` may be either an IP address or hostname.  If it's a hostname,
        the server will listen on all IP addresses associated with the name.
        set empty string or to listen on all available interfaces.
    :param bool debug: Tornado debug mode
    :param bool auto_open_webbrowser: Whether or not auto open web browser when server is started.
    :param str session_type: `Session <pywebio.session.AbstractSession>` 的实现，默认为基于线程的会话实现。
        接受的值为 `pywebio.session.THREAD_BASED` 和 `pywebio.session.COROUTINE_BASED`
    :param int websocket_max_message_size: Max bytes of a message which Tornado can accept.
        Messages larger than the ``websocket_max_message_size`` (default 10MiB) will not be accepted.
    :param int websocket_ping_interval: If set to a number, all websockets will be pinged every n seconds.
        This can help keep the connection alive through certain proxy servers which close idle connections,
        and it can detect if the websocket has failed without being properly closed.
    :param int websocket_ping_timeout: If the ping interval is set, and the server doesn’t receive a ‘pong’
        in this many seconds, it will close the websocket. The default is three times the ping interval,
        with a minimum of 30 seconds. Ignored if ``websocket_ping_interval`` is not set.
    :param tornado_app_settings: Additional keyword arguments passed to the constructor of ``tornado.web.Application``.
        ref: https://www.tornadoweb.org/en/stable/web.html#tornado.web.Application.settings
    :return:
    """
    kwargs = locals()

    mark_server_started(session_type)

    app_options = ['debug', 'websocket_max_message_size', 'websocket_ping_interval', 'websocket_ping_timeout']
    for opt in app_options:
        if kwargs[opt] is not None:
            tornado_app_settings[opt] = kwargs[opt]

    handler = webio_handler(target)
    _, port = _setup_server(webio_handler=handler, port=port, host=host, **tornado_app_settings)
    if auto_open_webbrowser:
        tornado.ioloop.IOLoop.current().spawn_callback(open_webbrowser_on_server_started, host or 'localhost', port)
    tornado.ioloop.IOLoop.current().start()


def start_server_in_current_thread_session():
    """启动 script mode 的server"""
    mark_server_started()

    websocket_conn_opened = threading.Event()
    thread = threading.current_thread()

    class SingleSessionWSHandler(webio_handler(None)):
        session = None

        def open(self):
            if SingleSessionWSHandler.session is None:
                SingleSessionWSHandler.session = DesignatedThreadSession(thread,
                                                                         on_task_command=self.send_msg_to_client,
                                                                         loop=asyncio.get_event_loop())
                websocket_conn_opened.set()
            else:
                self.close()

        def on_close(self):
            if SingleSessionWSHandler.session is not None:
                self.session.close()
                logger.debug('DesignatedThreadSession.closed')

    async def wait_to_stop_loop():
        alive_none_daemonic_thread_cnt = None
        while alive_none_daemonic_thread_cnt != 1:
            alive_none_daemonic_thread_cnt = sum(
                1 for t in threading.enumerate() if t.is_alive() and not t.isDaemon()
            )
            await asyncio.sleep(1)

        # Current thread is only one none-daemonic-thread, so exit
        logger.debug('Closing tornado ioloop...')
        tornado.ioloop.IOLoop.current().stop()

    def server_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        server, port = _setup_server(webio_handler=SingleSessionWSHandler, host='localhost')
        tornado.ioloop.IOLoop.current().spawn_callback(wait_to_stop_loop)
        tornado.ioloop.IOLoop.current().spawn_callback(open_webbrowser_on_server_started, 'localhost', port)

        tornado.ioloop.IOLoop.current().start()
        logger.debug('Tornado server exit')

    t = threading.Thread(target=server_thread, name='Tornado-server')
    t.start()

    websocket_conn_opened.wait()
