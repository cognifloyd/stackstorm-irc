# pylint: disable=super-on-old-class
import time
import random

import eventlet
from irc.bot import SingleServerIRCBot
from ib3.auth import SASL
from ib3.mixins import RejoinOnKick

from st2reactor.sensor.base import Sensor

eventlet.monkey_patch(
    os=True,
    select=True,
    socket=True,
    thread=True,
    time=True)


class StackStormSensorBaseBot(SingleServerIRCBot):
    """
    Base IRC mixin with common methods to dispatch StackStorm triggers based on IRC events.
    """
    def on_welcome(self, connection, event):
        self._logger.debug('Connected to the server')

        for channel in self._channels:
            self._logger.debug('Joining #%s...' % (channel))
            connection.join(channel)

    def on_nicknameinuse(self, connection, event):
        new_nickname = '%s-%s' % (connection.get_nickname(), random.randint(1, 1000))
        connection.nick(new_nickname)

    def on_pubmsg(self, connection, event):
        event.timestamp = int(time.time())
        handler = self._handlers.get('pubmsg', lambda connection, event: connection)
        handler(connection=connection, event=event)

    def on_privmsg(self, connection, event):
        event.timestamp = int(time.time())
        handler = self._handlers.get('privmsg', lambda connection, event: connection)
        handler(connection=connection, event=event)

    def on_join(self, connection, event):
        event.timestamp = int(time.time())
        handler = self._handlers.get('join', lambda connection, event: connection)
        handler(connection=connection, event=event)

    def on_part(self, connection, event):
        event.timestamp = int(time.time())
        handler = self._handlers.get('part', lambda connection, event: connection)
        handler(connection=connection, event=event)

class StackStormSensorSimpleBot(RejoinOnKick, StackStormSensorBaseBot):
    """
    Simple IRC Bot with no authentication.
    """
    def __init__(self, channels, handlers, logger, *args, **kwargs):
        super(StackStormSensorSimpleBot, self).__init__(*args, **kwargs)

        self._channels = channels
        self._handlers = handlers
        self._logger = logger

    def on_error(self, connection, event):
        """
        Parse server error message and terminate the bot if SASL authentication is requested.

        This is obvious for AWS-hosted servers, which IPs are blacklisted by IRC.freenode
        and registration + SASL auth is the only way to connect.
        """
        if 'SASL access only' in event.target:
            self._logger.error('This server requires SASL authentication only. '
                               'Please register and specify both nickname:password in config file.')
            self.die()


class StackStormSensorSaslBot(SASL, StackStormSensorBaseBot):
    """
    IRC bot using SASL authentication when the bot password is provided.
    http://ircv3.net/specs/extensions/sasl-3.1.html
    """
    def __init__(self, channels, handlers, logger, *args, **kwargs):
        super(StackStormSensorSaslBot, self).__init__(*args, **kwargs)
        self.connection.add_global_handler('904', self.on_sasl_failed)

        self._channels = channels
        self._handlers = handlers
        self._logger = logger

    def on_sasl_failed(self, connection, event):
        """
        Handle 904 ERR_SASLFAIL responses.
        Terminate the bot if invalid credentials were provided.
        """
        self._logger.error('SASL authentication failed! Please use correct username:password.'
                           'Additionally, make sure you registered your nickname at IRC server.')
        self.die()


class IRCSensor(Sensor):
    def __init__(self, sensor_service, config=None):
        super(IRCSensor, self).__init__(sensor_service=sensor_service,
                                        config=config)
        self._logger = self._sensor_service.get_logger(__name__)

        split = self._config['server'].split(':')
        self._server_host = split[0]
        self._server_port = int(split[1])
        self._nickname = self._config['nickname']
        self._password = self._config.get('password')
        self._channels = self._config['channels']

    def setup(self):
        handlers = {
            'pubmsg': self._handle_pubmsg,
            'privmsg': self._handle_privmsg,
            'join': self._handle_join,
            'part': self._handle_part
        }

        if self._password:
            self._bot = StackStormSensorSaslBot(server_list=[(self._server_host, self._server_port)],
                                                nickname=self._nickname,
                                                realname=self._nickname,
                                                ident_password=self._password,
                                                channels=self._channels,
                                                handlers=handlers,
                                                logger=self._logger)
        else:
            self._bot = StackStormSensorSimpleBot(server_list=[(self._server_host, self._server_port)],
                                                  nickname=self._nickname,
                                                  realname=self._nickname,
                                                  channels=self._channels,
                                                  handlers=handlers,
                                                  logger=self._logger)

    def run(self):
        self._bot.start()  # pylint: disable=no-member

    def cleanup(self):
        self._bot.disconnect(msg='Disconnecting')  # pylint: disable=no-member

    def add_trigger(self, trigger):
        pass

    def update_trigger(self, trigger):
        pass

    def remove_trigger(self, trigger):
        pass

    def _handle_pubmsg(self, connection, event):
        trigger = 'irc.pubmsg'

        payload = {
            'source': {
                'nick': event.source.nick,
                'host': event.source.host
            },
            'channel': event.target,
            'timestamp': event.timestamp,
            'message': event.arguments[0]
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload)

    def _handle_privmsg(self, connection, event):
        trigger = 'irc.privmsg'
        payload = {
            'source': {
                'nick': event.source.nick,
                'host': event.source.host
            },
            'timestamp': event.timestamp,
            'message': event.arguments[0]
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload)

    def _handle_join(self, connection, event):
        trigger = 'irc.join'
        payload = {
            'source': {
                'nick': event.source.nick,
                'host': event.source.host
            },
            'timestamp': event.timestamp,
            'channel': event.target
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload)

    def _handle_part(self, connection, event):
        trigger = 'irc.part'
        payload = {
            'source': {
                'nick': event.source.nick,
                'host': event.source.host
            },
            'timestamp': event.timestamp,
            'channel': event.target
        }
        self._sensor_service.dispatch(trigger=trigger, payload=payload)
