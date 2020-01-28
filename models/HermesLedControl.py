import json
import logging
import sys
import time

import paho.mqtt.client as mqtt
import re
from paho.mqtt.client import MQTTMessage

from models.LedsController import LedsController


class HermesLedControl:

	_SUB_ON_HOTWORD 				= 'hermes/hotword/+/detected'
	_SUB_ON_SAY 					= 'hermes/tts/say'
	_SUB_ON_THINK 					= 'hermes/asr/textCaptured'
	_SUB_ON_LISTENING 				= 'hermes/asr/startListening'
	_SUB_ON_HOTWORD_TOGGLE_ON 		= 'hermes/hotword/toggleOn'
	_SUB_LEDS_ON_ERROR 				= 'hermes/nlu/intentNotRecognized'
	_SUB_LEDS_ON_SUCCESS 			= 'hermes/nlu/intentParsed'
	_SUB_ON_PLAY_FINISHED 			= 'hermes/audioServer/{}/playFinished'
	_SUB_ON_TTS_FINISHED 			= 'hermes/tts/sayFinished'

	_SUB_ON_LEDS_TOGGLE 			= 'hermes/leds/toggle'
	_SUB_ON_LEDS_TOGGLE_ON 			= 'hermes/leds/toggleOn'
	_SUB_ON_LEDS_TOGGLE_OFF 		= 'hermes/leds/toggleOff'
	_SUB_ON_LEDS_CLEAR 				= 'hermes/leds/clear'
	_SUB_UPDATING 					= 'hermes/leds/systemUpdate'
	_SUB_ON_CALL 					= 'hermes/leds/onCall'
	_SUB_SETUP_MODE 				= 'hermes/leds/setupMode'
	_SUB_CON_ERROR 					= 'hermes/leds/connectionError'
	_SUB_ON_MESSAGE 				= 'hermes/leds/onMessage'
	_SUB_ON_DND 					= 'hermes/leds/doNotDisturb'
	_SUB_ON_START 					= 'hermes/leds/onStart'
	_SUB_ON_STOP 					= 'hermes/leds/onStop'

	_SUB_VOLUME_SET 				= 'hermes/volume/set'
	_SUB_VADLED_SET 				= 'hermes/leds/vadLed'


	def __init__(self, params):
		self._logger = logging.getLogger('HermesLedControl')
		self._logger.info('Initializing HermesLedControl')

		self._mqttClient 			= None
		self._hardwareReference 	= None
		self._ledsController 		= None
		self._params 				= params

		self._mqttServer 			= 'localhost'
		self._me 					= 'default'
		self._mqttPort 				= 1883
		self._mqttUsername 			= ''
		self._mqttPassword 			= ''
		self._tlsFile 				= ''

		self._hotwordRegex          = re.compile(self._SUB_ON_HOTWORD.replace('+', '(.*)'))

		if params.engine == 'projectalice':
			from models.engines.ProjectAlice import ProjectAlice
			engine = ProjectAlice()
		elif params.engine == 'rhasspy':
			from models.engines.Rhasspy import Rhasspy
			engine = Rhasspy()
		elif params.engine == 'snips':
			from models.engines.Snips import Snips
			engine = Snips()
		else:
			self._logger.error('Unsupported assistant engine {params.engine}')
			self.onStop()
			return

		self._configs = engine.loadConfig(params)
		if not self._configs:
			self.onStop()

		with open('hardware.json') as f:
			self._hardwareReference = json.load(f)
			self._logger.info('Loaded {} hardware references'.format(len(self._hardwareReference)))

		if params.hardware not in self._hardwareReference:
			self._logger.fatal('Trying to use an unsupported hardware')
			self.onStop()
		else:
			self._hardware = self._hardwareReference[self._params.hardware]

		self._mqttServer = params.mqttServer or self._configs['mqttServer']
		self._mqttPort = int(params.mqttPort or self._configs['mqttPort'])
		self._mqttUsername = params.mqttUsername or self._configs['mqttUsername']
		self._mqttPassword = params.mqttPassword or self._configs['mqttPassword']
		self._tlsFile = self._configs['mqttTLSCAFile']
		self._me = params.clientId or self._configs['deviceName']

		self._SUB_ON_PLAY_FINISHED = self._SUB_ON_PLAY_FINISHED.format(self._me)

		self._logger.info('- Mqtt server set to {}'.format(self._mqttServer))
		self._logger.info('- Mqtt port set to {}'.format(self._mqttPort))

		if self._mqttUsername:
			self._logger.info('- Mqtt username set to {}'.format(self._mqttUsername))
		if self._mqttPassword:
			self._logger.info('- Mqtt password set to "hidden"')

		self._logger.info('- Client id set to {}'.format(self._me))
		self._logger.info('- Hardware set to {}'.format(self._hardware['name']))

		if params.leds is not None:
			self._hardware['numberOfLeds'] = params.leds
		self._logger.info('- Using {} as pattern with {} leds'.format(params.pattern, self._hardware['numberOfLeds']))

		if 'gpioPin' in self._hardware:
			if params.gpioPin is not None:
				self._hardware['gpioPin'] = params.gpioPin
			self._logger.info('Using pin #{}'.format(self._hardware['gpioPin']))

		if 'vid' in self._hardware and params.vid is not None:
			self._hardware['vid'] = params.vid

		if 'gpios' in self._hardware and len(params.pureGpioPinout) > 0:
			self.hardware['gpios'] = params.pureGpioPinout

		if 'activeHigh' in self._hardware:
			self._hardware['activeHigh'] = params.activeHigh

		if 'endFrame' in self._hardware and params.endFrame is not None:
			self._hardware['endFrame'] = params.endFrame


		self._ledsController = LedsController(self)
		self._mqttClient = self.connectMqtt()


	def onStart(self):
		self._ledsController.onStart()
		self._logger.info('Hermes Led Control started')


	def onStop(self):
		if self._mqttClient is not None:
			self._mqttClient.disconnect()

		if self._ledsController is not None:
			self._ledsController.onStop()

		sys.exit(0)


	def connectMqtt(self):
		try:
			mqttClient = mqtt.Client()

			if self._mqttUsername and self._mqttPassword:
				mqttClient.username_pw_set(self._mqttUsername, self._mqttPassword)

			mqttClient.on_log = self.onLog
			mqttClient.on_connect = self.onConnect
			mqttClient.on_message = self.onMessage

			if self._tlsFile:
				mqttClient.tls_set(certfile=self._tlsFile)
				mqttClient.tls_insecure_set(False)

			mqttClient.connect(self._mqttServer, int(self._mqttPort))
			mqttClient.loop_start()
			return mqttClient
		except:
			self._logger.fatal("Couldn't connect to mqtt, aborting")
			self.onStop()


	# noinspection PyUnusedLocal
	def onLog(self, client, userdata, level, buf):
		if level != 16:
			self._logger.error(buf)


	# noinspection PyUnusedLocal
	def onConnect(self, client, userdata, flags, rc):
		time.sleep(0.1)
		self._mqttClient.subscribe([
			(self._SUB_ON_HOTWORD, 0),
			(self._SUB_ON_SAY, 0),
			(self._SUB_ON_THINK, 0),
			(self._SUB_ON_LISTENING, 0),
			(self._SUB_ON_PLAY_FINISHED, 0),
			(self._SUB_ON_TTS_FINISHED, 0),
			(self._SUB_ON_LEDS_TOGGLE_ON, 0),
			(self._SUB_ON_LEDS_TOGGLE_OFF, 0),
			(self._SUB_ON_LEDS_TOGGLE, 0),
			(self._SUB_LEDS_ON_ERROR, 0),
			(self._SUB_LEDS_ON_SUCCESS, 0),
			(self._SUB_ON_START, 0),
			(self._SUB_ON_STOP, 0),
			(self._SUB_UPDATING, 0),
			(self._SUB_ON_CALL, 0),
			(self._SUB_SETUP_MODE, 0),
			(self._SUB_CON_ERROR, 0),
			(self._SUB_ON_MESSAGE, 0),
			(self._SUB_ON_DND, 0),
			(self._SUB_VOLUME_SET, 0),
			(self._SUB_VADLED_SET, 0),
			(self._SUB_ON_LEDS_CLEAR, 0)
		])

		self._mqttClient.subscribe(self._params.offListener)


	# noinspection PyUnusedLocal
	def onMessage(self, client, userdata, message: MQTTMessage):
		payload = dict()

		if hasattr(message, 'payload') and message.payload:
			payload = json.loads(message.payload.decode('UTF-8'))

		siteId = payload.get('siteId')
		sticky = 'sticky' in payload


		if self._hotwordRegex.match(message.topic):
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On hotword triggered')
				self._ledsController.wakeup(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On hotword received but it wasn't for me")

		elif message.topic == self._SUB_ON_LISTENING:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On listen triggered')
				self._ledsController.listen(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On listen received but it wasn't for me")

		elif message.topic == self._SUB_ON_SAY:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On say triggered')
				self._ledsController.speak(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On say received but it wasn't for me")

		elif message.topic == self._SUB_ON_THINK:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On think triggered')
				self._ledsController.think(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On think received but it wasn't for me")

		elif message.topic == self._SUB_ON_HOTWORD_TOGGLE_ON:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On hotword toggle on triggered')
				self._ledsController.idle()
			else:
				if self._params.debug:
					self._logger.debug("On hotword toggle on received but it wasn't for me")

		elif message.topic == self._SUB_ON_TTS_FINISHED:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On tts finished triggered')
				self._ledsController.idle()
			else:
				if self._params.debug:
					self._logger.debug("On tts finished received but it wasn't for me")

		elif message.topic == self._SUB_ON_PLAY_FINISHED:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On play finished triggered')
				self._ledsController.idle()
			else:
				if self._params.debug:
					self._logger.debug("On play finished received but it wasn't for me")

		elif message.topic == self._SUB_ON_LEDS_TOGGLE_ON:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On leds toggle on triggered')
				self._ledsController.toggleStateOn()
			else:
				if self._params.debug:
					self._logger.debug("On leds toggle on received but it wasn't for me")

		elif message.topic == self._SUB_ON_LEDS_TOGGLE_OFF:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On leds toggle off triggered')
				self._ledsController.toggleStateOff()
			else:
				if self._params.debug:
					self._logger.debug("On leds toggle off received but it wasn't for me")

		elif message.topic == self._SUB_ON_LEDS_TOGGLE:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On leds toggle triggered')
				self._ledsController.toggleState()
			else:
				if self._params.debug:
					self._logger.debug("On leds toggle received but it wasn't for me")

		elif message.topic == self._SUB_LEDS_ON_SUCCESS:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On success triggered')
				self._ledsController.onSuccess(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On success received but it wasn't for me")

		elif message.topic == self._SUB_LEDS_ON_ERROR:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On error triggered')
				self._ledsController.onError(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On error received but it wasn't for me")

		elif message.topic == self._SUB_UPDATING:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On updating triggered')
				self._ledsController.updating(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On updating received but it wasn't for me")

		elif message.topic == self._SUB_ON_CALL:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On call triggered')
				self._ledsController.call(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On call received but it wasn't for me")

		elif message.topic == self._SUB_SETUP_MODE:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On setup mode triggered')
				self._ledsController.setupMode(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On setup mode received but it wasn't for me")

		elif message.topic == self._SUB_CON_ERROR:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On connection error triggered')
				self._ledsController.conError(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On connection error received but it wasn't for me")

		elif message.topic == self._SUB_ON_MESSAGE:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On message triggered')
				self._ledsController.message(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On message received but it wasn't for me")

		elif message.topic == self._SUB_ON_DND:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On do not disturb triggered')
				self._ledsController.dnd(sticky)
			else:
				if self._params.debug:
					self._logger.debug("On do not disturb received but it wasn't for me")

		elif message.topic == self._SUB_ON_START:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On start triggered')
				self._ledsController.start()
			else:
				if self._params.debug:
					self._logger.debug("On start received but it wasn't for me")

		elif message.topic == self._SUB_ON_STOP:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On stop triggered')
				self._ledsController.stop()
			else:
				if self._params.debug:
					self._logger.debug("On stop received but it wasn't for me")

		elif message.topic == self._SUB_VOLUME_SET:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On volume set triggered')
				if 'volume' not in payload:
					self._logger.error('Missing "volume" in payload for set volume')
				else:
					self._ledsController.setVolume(payload['volume'])
			else:
				if self._params.debug:
					self._logger.debug("On volume set received but it wasn't for me")

		elif message.topic == self._SUB_VADLED_SET:
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On vad led set triggered')
				if 'state' not in payload:
					self._logger.error('Missing "state" in payload for set vad led')
				else:
					self._ledsController.setVadLed(payload['state'])
			else:
				if self._params.debug:
					self._logger.debug("On vad led set received but it wasn't for me")

		elif message.topic == self._SUB_ON_LEDS_CLEAR:
			self._ledsController.stickyAnimation = None
			if siteId == self._me:
				if self._params.debug:
					self._logger.debug('On leds clear triggered')
				else:
					self._ledsController.clearLeds()
			else:
				if self._params.debug:
					self._logger.debug("On leds clear received but it wasn't for me")


	@property
	def params(self):
		return self._params


	@property
	def hardwareReference(self) -> dict:
		return self._hardwareReference


	@property
	def hardware(self) -> dict:
		return self._hardware
